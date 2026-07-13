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


# ---------- 国能 e 招计划追踪 ----------


OpportunityWatchRunStatus = Literal[
    "queued", "running", "succeeded", "partial", "failed"
]
OpportunityWatchExtractionStatus = Literal["resolved", "needs_review"]
# 同步错误码固定字典；读模型与 ORM 约束保持一致，禁止透传远端错误文本。
OpportunityWatchErrorCode = Literal[
    "source_unavailable",
    "rate_limited",
    "malformed_response",
    "interrupted",
]


class OpportunityWatchPlanOut(BaseModel):
    """
    模块：国能 e 招计划追踪读模型
    用途：工作空间内的计划追踪读模型；不含上传文件或外部请求字段。
    对接：BidWatchPlanRow；opportunity_watch_service.list_watch_plans；当前尚无公开 HTTP 路由。
    二次开发：不得加入 URL、Cookie、HTML、上传文件或任意远端地址字段；后续路由序列化时复用本模型。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    title: str
    buyer: str
    scope: str
    duration: str
    expected_publish_text: str = Field(serialization_alias="expectedPublishText")
    remark: str
    fingerprint: str
    enabled: bool
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class OpportunityWatchSyncRunOut(BaseModel):
    """
    模块：国能 e 招同步运行读模型
    用途：受控同步的脱敏运行读模型；errorCode 仅允许服务端固定码。
    对接：BidSourceSyncRunRow；opportunity_watch_service.list_watch_runs；当前尚无公开 HTTP 路由。
    二次开发：errorCode 仅允许固定枚举或 None；不得加入远端错误原文、URL、Cookie 或 HTML。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    source_name: Literal["chnenergy"] = Field(serialization_alias="sourceName")
    status: OpportunityWatchRunStatus
    started_at: datetime = Field(serialization_alias="startedAt")
    finished_at: datetime | None = Field(default=None, serialization_alias="finishedAt")
    plan_count: int = Field(serialization_alias="planCount")
    candidate_count: int = Field(serialization_alias="candidateCount")
    detail_page_count: int = Field(serialization_alias="detailPageCount")
    resolved_count: int = Field(serialization_alias="resolvedCount")
    needs_review_count: int = Field(serialization_alias="needsReviewCount")
    skipped_count: int = Field(serialization_alias="skippedCount")
    error_code: OpportunityWatchErrorCode | None = Field(
        default=None, serialization_alias="errorCode"
    )
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class OpportunityWatchHitOut(BaseModel):
    """
    模块：国能 e 招公告命中读模型
    用途：公告命中脱敏读模型；不持久化、不序列化详情链接或外部正文。
    对接：BidSourceHitRow；opportunity_watch_service.list_watch_hits；当前尚无公开 HTTP 路由。
    二次开发：详情链接若需展示，须由后续路由按固定来源客户端动态生成，不得写入本模型字段。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    watch_plan_id: str = Field(serialization_alias="watchPlanId")
    sync_run_id: str = Field(serialization_alias="syncRunId")
    source_name: Literal["chnenergy"] = Field(serialization_alias="sourceName")
    source_info_id: str = Field(serialization_alias="sourceInfoId")
    category_num: str = Field(serialization_alias="categoryNum")
    source_publish_text: str = Field(serialization_alias="sourcePublishText")
    title: str
    deadline_at_local: str | None = Field(default=None, serialization_alias="deadlineAtLocal")
    opening_at_local: str | None = Field(default=None, serialization_alias="openingAtLocal")
    source_timezone: Literal["Asia/Shanghai"] = Field(serialization_alias="sourceTimezone")
    extraction_status: OpportunityWatchExtractionStatus = Field(serialization_alias="extractionStatus")
    accepted_opportunity_id: str | None = Field(
        default=None, serialization_alias="acceptedOpportunityId"
    )
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class OpportunityWatchAcceptOut(BaseModel):
    """
    模块：国能 e 招人工接受命中结果
    用途：人工接受公告命中后的本地标讯结果；不包含外部正文或链接。
    对接：POST /api/opportunity-watch/hits/{hit_id}/accept；accept_watch_hit。
    二次开发：不得回传 HTML、JSON、Cookie 或任意 URL；仅 opportunityId 与 created。
    """

    model_config = ConfigDict(populate_by_name=True)

    opportunity_id: str = Field(
        alias="opportunityId", serialization_alias="opportunityId"
    )
    created: bool


class OpportunityWatchPlanImportOut(BaseModel):
    """
    模块：国能 e 招计划表导入统计
    用途：仅返回本机 .xlsx 计划导入的插入/跳过/总数；非法整批不返回成功统计。
    对接：POST /api/opportunity-watch/plans/import；opportunity_watch_service.import_watch_plans_from_xlsx。
    二次开发：禁止增加文件名、路径、工作簿内容、URL、Cookie 或任意远端字段。
    """

    inserted: int
    skipped: int
    total: int


class OpportunityWatchSyncAcceptedOut(BaseModel):
    """
    模块：国能 e 招同步受理响应
    用途：POST /sync 仅返回 runId；不包含 URL、Cookie、错误原文或请求回显。
    对接：POST /api/opportunity-watch/sync → 202；BackgroundTasks 执行器。
    二次开发：禁止扩展为可传入搜索条件、主机或凭据的请求模型。
    """

    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId", serialization_alias="runId")


class OpportunityWatchDashboardHitOut(BaseModel):
    """
    模块：国能 e 招仪表盘命中读模型
    用途：dashboard 命中列表项；结构化字段 + 服务端动态生成的 announcementUrl。
    对接：GET /api/opportunity-watch/dashboard；get_watch_dashboard。
    二次开发：announcementUrl 仅由后端按固定规则生成；禁止前端提交、禁止持久化该字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    watch_plan_id: str = Field(serialization_alias="watchPlanId")
    sync_run_id: str = Field(serialization_alias="syncRunId")
    source_name: Literal["chnenergy"] = Field(serialization_alias="sourceName")
    source_info_id: str = Field(serialization_alias="sourceInfoId")
    category_num: str = Field(serialization_alias="categoryNum")
    source_publish_text: str = Field(serialization_alias="sourcePublishText")
    title: str
    deadline_at_local: str | None = Field(
        default=None, serialization_alias="deadlineAtLocal"
    )
    opening_at_local: str | None = Field(
        default=None, serialization_alias="openingAtLocal"
    )
    source_timezone: Literal["Asia/Shanghai"] = Field(
        serialization_alias="sourceTimezone"
    )
    extraction_status: OpportunityWatchExtractionStatus = Field(
        serialization_alias="extractionStatus"
    )
    accepted_opportunity_id: str | None = Field(
        default=None, serialization_alias="acceptedOpportunityId"
    )
    announcement_url: str | None = Field(
        default=None, serialization_alias="announcementUrl"
    )
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class OpportunityWatchDashboardOut(BaseModel):
    """
    模块：国能 e 招仪表盘只读聚合
    用途：返回当前工作空间计划数、最近运行与命中列表；无同步/接受副作用。
    对接：GET /api/opportunity-watch/dashboard；get_watch_dashboard。
    二次开发：禁止增加 Cookie、HTML、原文、任意 URL 入参或写入字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    plan_count: int = Field(serialization_alias="planCount")
    latest_run: OpportunityWatchSyncRunOut | None = Field(
        default=None, serialization_alias="latestRun"
    )
    hits: list[OpportunityWatchDashboardHitOut]


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


# ---------- P9C 离线语义索引 ----------


SemanticIndexStatus = Literal[
    "queued", "running", "active", "failed", "superseded", "index_not_built"
]
SemanticIndexErrorCode = Literal[
    "model_unavailable",
    "model_storage_insufficient",
    "index_interrupted",
    "index_failed",
    "index_not_built",
    "index_building",
]
SemanticSearchStatus = Literal[
    "ready",
    "model_unavailable",
    "model_storage_insufficient",
    "index_interrupted",
    "index_failed",
    "index_not_built",
    "index_building",
]


class SemanticIndexOut(BaseModel):
    """
    模块：P9C 语义索引读模型
    用途：版本化离线索引状态；不含路径、密钥、正文或远端错误原文。
    对接：GET/POST /api/knowledge/semantic-index*；knowledge_service.semantic_index_to_dict。
    二次开发：禁止加入 modelUrl、apiKey、cachePath 或任意用户可填向量配置字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    workspace_id: str | None = Field(default=None, serialization_alias="workspaceId")
    status: str
    provider: Literal["offline_bge"] = "offline_bge"
    model_id: str = Field(serialization_alias="modelId")
    model_fingerprint: str | None = Field(
        default=None, serialization_alias="modelFingerprint"
    )
    dimension: int = 512
    # 构建进度：total=待嵌入总数；embedded=已写入；chunkCount 兼容等价 embeddedChunks
    total_chunks: int = Field(default=0, serialization_alias="totalChunks")
    embedded_chunks: int = Field(default=0, serialization_alias="embeddedChunks")
    chunk_count: int = Field(default=0, serialization_alias="chunkCount")
    error_code: SemanticIndexErrorCode | None = Field(
        default=None, serialization_alias="errorCode"
    )
    started_at: datetime | None = Field(default=None, serialization_alias="startedAt")
    finished_at: datetime | None = Field(default=None, serialization_alias="finishedAt")
    created_at: datetime | None = Field(default=None, serialization_alias="createdAt")
    updated_at: datetime | None = Field(default=None, serialization_alias="updatedAt")


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


# ---------- 技术标中标内容模板 ----------


TemplateStatus = Literal["active", "archived"]
TemplateKind = Literal["technical"]


class BidTemplateSummaryOut(BaseModel):
    """
    模块：中标内容模板列表摘要
    用途：GET /api/templates 列表读模型；仅元数据 + 轻量展示摘要，不含完整 snapshot。
    对接：/api/templates；frontend bid-templates 列表卡。
    二次开发：勿回填完整 snapshot；完整快照仅走详情/沉淀响应。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    title: str
    tags: list[str] = Field(default_factory=list)
    status: TemplateStatus = "active"
    kind: TemplateKind = "technical"
    source_project_id: str | None = Field(
        default=None, serialization_alias="sourceProjectId"
    )
    source_project_name: str = Field(
        default="", serialization_alias="sourceProjectName"
    )
    chapter_count: int = Field(default=0, serialization_alias="chapterCount")
    outline_titles: list[str] = Field(
        default_factory=list, serialization_alias="outlineTitles"
    )
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class BidTemplateOut(BaseModel):
    """
    模块：中标内容模板详情响应
    用途：详情/沉淀响应读模型；snapshot 为独立深拷贝，非源项目 live 引用。
    对接：GET /api/templates/{id}；POST /api/templates/from-project。
    二次开发：勿与导出版式模板字段混用；列表禁止复用本模型以免拖入完整快照。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    title: str
    tags: list[str] = Field(default_factory=list)
    status: TemplateStatus = "active"
    kind: TemplateKind = "technical"
    source_project_id: str | None = Field(
        default=None, serialization_alias="sourceProjectId"
    )
    source_project_name: str = Field(
        default="", serialization_alias="sourceProjectName"
    )
    snapshot: dict
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class BidTemplateFromProjectCreate(BaseModel):
    """
    用途：从技术标项目沉淀模板请求体。
    对接：POST /api/templates/from-project。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    title: str | None = None
    tags: list[str] | None = None


class BidTemplateProjectCreate(BaseModel):
    """
    用途：从模板创建全新技术标项目草稿请求体。
    对接：POST /api/templates/{id}/projects。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    industry: str | None = None


KnowledgeCardType = Literal["document", "image", "qualification", "performance"]
KnowledgeCardStatus = Literal["active", "archived"]
# 列表筛选：缺省等价 active；all 返回含归档的全量
KnowledgeCardListStatus = Literal["active", "archived", "all"]


class KnowledgeCardSummaryOut(BaseModel):
    """
    模块：知识卡片列表摘要
    用途：GET /api/cards 列表读模型；不含正文全文与图片 base64。
    对接：/api/cards；frontend knowledge-base 卡片 Tab。
    二次开发：完整正文与图片元数据仅走详情；勿回填 base64。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    type: KnowledgeCardType
    title: str
    tags: list[str] = Field(default_factory=list)
    status: KnowledgeCardStatus = "active"
    summary: str = ""
    source_type: str = Field(default="manual", serialization_alias="sourceType")
    source_id: str | None = Field(default=None, serialization_alias="sourceId")
    source_label: str = Field(default="", serialization_alias="sourceLabel")
    has_body: bool = Field(default=False, serialization_alias="hasBody")
    has_image: bool = Field(default=False, serialization_alias="hasImage")
    content_type: str | None = Field(default=None, serialization_alias="contentType")
    size_bytes: int = Field(default=0, serialization_alias="sizeBytes")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class KnowledgeCardOut(BaseModel):
    """
    模块：知识卡片详情
    用途：详情/创建/更新响应；含正文快照与图片元数据，不含 base64。
    对接：GET /api/cards/{id}；POST /api/cards*。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    type: KnowledgeCardType
    title: str
    tags: list[str] = Field(default_factory=list)
    status: KnowledgeCardStatus = "active"
    summary: str = ""
    source_type: str = Field(default="manual", serialization_alias="sourceType")
    source_id: str | None = Field(default=None, serialization_alias="sourceId")
    source_label: str = Field(default="", serialization_alias="sourceLabel")
    has_body: bool = Field(default=False, serialization_alias="hasBody")
    has_image: bool = Field(default=False, serialization_alias="hasImage")
    content_type: str | None = Field(default=None, serialization_alias="contentType")
    size_bytes: int = Field(default=0, serialization_alias="sizeBytes")
    body_markdown: str = Field(default="", serialization_alias="bodyMarkdown")
    payload: dict | None = None
    stored_name: str | None = Field(default=None, serialization_alias="storedName")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class KnowledgeCardCreate(BaseModel):
    """
    用途：手工创建文本类卡片请求体。
    对接：POST /api/cards。
    """

    model_config = ConfigDict(populate_by_name=True)

    type: KnowledgeCardType = "document"
    title: str
    body_markdown: str = Field(alias="bodyMarkdown")
    tags: list[str] | None = None
    summary: str | None = None
    source_label: str | None = Field(default=None, alias="sourceLabel")
    payload: dict | None = None


class KnowledgeCardFromChunkCreate(BaseModel):
    """
    用途：从知识分块沉淀卡片。
    对接：POST /api/cards/from-chunk。
    """

    model_config = ConfigDict(populate_by_name=True)

    chunk_id: str = Field(alias="chunkId")
    title: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
    type: KnowledgeCardType = "document"


class KnowledgeCardFromProjectImageCreate(BaseModel):
    """
    用途：从项目图片沉淀卡片。
    对接：POST /api/cards/from-project-image。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    file_id: str = Field(alias="fileId")
    title: str | None = None
    tags: list[str] | None = None
    summary: str | None = None


class KnowledgeCardUpdate(BaseModel):
    """
    用途：更新卡片元数据/正文。
    对接：PATCH /api/cards/{id}。
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    tags: list[str] | None = None
    status: KnowledgeCardStatus | None = None
    summary: str | None = None
    body_markdown: str | None = Field(default=None, alias="bodyMarkdown")
    source_label: str | None = Field(default=None, alias="sourceLabel")
    payload: dict | None = None


class InsertCardBody(BaseModel):
    """
    用途：将卡片转为可插入章节的 Markdown 片段请求体。
    对接：POST /api/projects/{projectId}/insert-card。
    """

    model_config = ConfigDict(populate_by_name=True)

    card_id: str = Field(alias="cardId")


class InsertCardOut(BaseModel):
    """
    用途：插入卡片结果；markdown 由前端用户操作追加，服务端不自动覆盖正文。
    对接：POST /api/projects/{projectId}/insert-card。
    """

    model_config = ConfigDict(populate_by_name=True)

    markdown: str
    project_image_id: str | None = Field(
        default=None, serialization_alias="projectImageId"
    )
    card_id: str = Field(serialization_alias="cardId")
    card_type: KnowledgeCardType = Field(serialization_alias="cardType")
    title: str
    source_label: str = Field(default="", serialization_alias="sourceLabel")
