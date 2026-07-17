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
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


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


class ParseStrategyOut(BaseModel):
    """
    模块：解析策略脱敏响应
    用途：仅序列化 parseStrategy，供技术标/商务标入口读取默认策略。
    对接：GET /api/settings/parse-strategy；P8B 契约字段白名单。
    二次开发：禁止追加 apiKey、provider、model、embedding、workspaceId 等任何设置字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    parse_strategy: str = Field(serialization_alias="parseStrategy")


# ---------- LLM / revise ----------


class LlmTestOut(BaseModel):
    """用途：连通性测试结果。"""

    ok: bool
    model: str
    reply: str


class ReviseIn(BaseModel):
    """
    用途：定向修订请求。
    对接：docs/ai-feedback-loop.md；前端 submitRevise；P12B-C1 商务写阶段强制版本。
    二次开发：business_parse|qualify|toc|quote|commit 强制合法 expectedStateVersion；
      仅预览的技术修订 stage 保持兼容。
    """

    model_config = ConfigDict(populate_by_name=True)

    stage: str
    message: str
    preserve_structure: bool = Field(default=True, alias="preserveStructure")
    base_content: str | None = Field(default=None, alias="baseContent")
    guidance: dict | None = None
    target_id: str | None = Field(default=None, alias="targetId")
    target_label: str | None = Field(default=None, alias="targetLabel")
    expected_state_version: str | None = Field(
        default=None,
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )

    @model_validator(mode="after")
    def _require_expected_for_business_writers(self) -> "ReviseIn":
        """用途：会写 editor-state 的商务 stage 强制合法 expected。"""
        write_stages = {
            "business_parse",
            "business_qualify",
            "business_toc",
            "business_quote",
            "business_commit",
        }
        if self.stage in write_stages and not self.expected_state_version:
            raise ValueError(
                "business_parse/qualify/toc/quote/commit 阶段必须提供合法 expectedStateVersion"
            )
        return self


class ReviseOut(BaseModel):
    """
    用途：修订结果；兼容前端 AiFeedbackRecord 字段 + 可选修订正文。
    二次开发：商务写阶段成功时含服务端新 stateVersion。
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
    state_version: str | None = Field(
        default=None,
        serialization_alias="stateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )
    project_id: str | None = Field(default=None, serialization_alias="projectId")


class EditorStateOut(BaseModel):
    """
    用途：编辑器整包状态（技术标 outline/chapters/responseMatrix + 商务标 business*）。
    对接：useTechnicalPlanEditors / useProjectGuidance / useBusinessBidWorkspace
    二次开发：
      - responseMatrixVersion 仅表示收敛后的矩阵内容版本，与 updatedAt 无关。
      - stateVersion 为全状态 13 键规范哈希，与 P12A 检查点版本算法一致。
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
    state_version: str = Field(default="", serialization_alias="stateVersion")
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
    二次开发：
      - 同时传 responseMatrix + responseMatrixVersion 时启用矩阵乐观锁；仅矩阵 null 不更新。
      - 可选 expectedStateVersion 启用全状态 CAS；格式非法固定 422 且不进 service。
      - 缺 expected 保持兼容写入，非最终安全恢复门。
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
    expected_state_version: str | None = Field(
        default=None,
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
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


# ---------- P10A 本机身份与会话 ----------


AuthRole = Literal["bid_writer", "finance", "hr", "bidder"]


class AuthBootstrapStatusOut(BaseModel):
    """
    用途：公开引导与部署模式握手；不含用户、会话或密钥。
    authRequired 严格等于服务端已校验 auth_mode == required。
    """

    model_config = ConfigDict(populate_by_name=True)

    bootstrapped: bool
    auth_required: bool = Field(
        alias="authRequired", serialization_alias="authRequired"
    )


class AuthLoginRequest(BaseModel):
    """用途：登录请求；仅用户名与口令。"""

    model_config = ConfigDict(populate_by_name=True)

    username: str
    password: str


class AuthUserOut(BaseModel):
    """用途：脱敏用户。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    username: str


class AuthWorkspaceOut(BaseModel):
    """用途：当前用户可访问的工作空间成员视图。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    role: AuthRole
    is_owner: bool = Field(alias="isOwner", serialization_alias="isOwner")


class AuthMeOut(BaseModel):
    """
    用途：登录/me/切换空间的脱敏响应。
    csrfToken 仅登录时非空；后续 GET /me 可为 null（客户端内存持有）。
    """

    model_config = ConfigDict(populate_by_name=True)

    user: AuthUserOut
    workspaces: list[AuthWorkspaceOut]
    active_workspace_id: str | None = Field(
        alias="activeWorkspaceId", serialization_alias="activeWorkspaceId"
    )
    csrf_token: str | None = Field(
        default=None, alias="csrfToken", serialization_alias="csrfToken"
    )


class AuthCsrfOut(BaseModel):
    """
    用途：硬刷新后 CSRF 续发响应；仅含一次下发的原始 csrfToken。
    对接：GET /api/auth/csrf；响应必须 Cache-Control: no-store。
    二次开发：禁止附带用户、会话摘要、Cookie 或口令字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    csrf_token: str = Field(alias="csrfToken", serialization_alias="csrfToken")


class ActiveWorkspaceUpdate(BaseModel):
    """用途：切换会话活动工作空间。"""

    model_config = ConfigDict(populate_by_name=True)

    workspace_id: str = Field(alias="workspaceId")


class AuthMemberOut(BaseModel):
    """
    用途：工作空间成员脱敏响应；不含口令/摘要/会话。
    对接：GET/POST/PATCH /api/auth/members*。
    """

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId", serialization_alias="userId")
    username: str
    role: AuthRole
    is_owner: bool = Field(alias="isOwner", serialization_alias="isOwner")
    is_active: bool = Field(alias="isActive", serialization_alias="isActive")
    created_at: datetime = Field(alias="createdAt", serialization_alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt", serialization_alias="updatedAt")


class AuthMemberCreate(BaseModel):
    """
    用途：所有者创建本机用户并加入当前工作空间。
    对接：POST /api/auth/members；禁止公开注册。
    """

    model_config = ConfigDict(populate_by_name=True)

    username: str
    password: str
    role: AuthRole
    is_owner: bool = Field(default=False, alias="isOwner")


class AuthMemberUpdate(BaseModel):
    """
    用途：所有者最小更新成员；至少一项 role/isOwner/isActive。
    对接：PATCH /api/auth/members/{user_id}。
    """

    model_config = ConfigDict(populate_by_name=True)

    role: AuthRole | None = None
    is_owner: bool | None = Field(default=None, alias="isOwner")
    is_active: bool | None = Field(default=None, alias="isActive")


# ---------- P10B 财务只读商务投标报价 ----------


class FinanceQuoteRowOut(BaseModel):
    """
    模块：财务报价分项行
    用途：明细接口中单行报价的白名单投影；amount 仅有限数值或 null。
    对接：GET /api/finance/business-bids/{project_id} → quoteRows。
    二次开发：禁止附加成本/利润/税率或透传 business_json 额外键。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    unit: str
    quantity: str
    unit_price: str = Field(serialization_alias="unitPrice")
    amount: float | None = None
    remark: str


class FinanceBusinessBidSummaryOut(BaseModel):
    """
    模块：财务商务标报价列表项
    用途：列表接口的项目摘要 + 报价行数与合计。
    对接：GET /api/finance/business-bids。
    二次开发：字段集合为契约白名单，不得扩展敏感业务字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    name: str
    industry: str
    status: str
    updated_at: datetime = Field(serialization_alias="updatedAt")
    quote_row_count: int = Field(serialization_alias="quoteRowCount")
    quote_total: float = Field(serialization_alias="quoteTotal")


class FinanceBusinessBidListOut(BaseModel):
    """
    模块：财务商务标报价列表响应
    用途：包装 items 数组，避免裸列表难以演进。
    对接：GET /api/finance/business-bids。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[FinanceBusinessBidSummaryOut]


class FinanceBusinessBidDetailOut(FinanceBusinessBidSummaryOut):
    """
    模块：财务商务标报价明细响应
    用途：在列表字段上追加 quoteRows 与 quoteNotes。
    对接：GET /api/finance/business-bids/{project_id}。
    二次开发：禁止附带 qualify/toc/commit/技术标/文件/设置。
    """

    quote_rows: list[FinanceQuoteRowOut] = Field(serialization_alias="quoteRows")
    quote_notes: str = Field(serialization_alias="quoteNotes")


# ---------- P10C 财务成本草案 ----------


FinanceCostCategory = Literal["labor", "material", "service", "other"]


class FinanceCostEntryOut(BaseModel):
    """
    模块：财务成本条目响应
    用途：单条成本草案白名单字段；不含创建人与工作空间。
    对接：POST/PATCH 成本条目与 GET cost-draft.costEntries。
    二次开发：禁止附加审批/税务/用户隐私字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    category: FinanceCostCategory
    name: str
    amount_fen: int = Field(serialization_alias="amountFen")
    remark: str
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class FinanceCostDraftOut(BaseModel):
    """
    模块：财务成本草案汇总响应
    用途：报价分、成本分、毛利分、基点与条目列表。
    对接：GET /api/finance/business-bids/{project_id}/cost-draft。
    二次开发：字段集合为契约白名单；不得附带报价行或 business_json。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    project_name: str = Field(serialization_alias="projectName")
    quote_total_fen: int = Field(serialization_alias="quoteTotalFen")
    cost_total_fen: int = Field(serialization_alias="costTotalFen")
    gross_profit_fen: int = Field(serialization_alias="grossProfitFen")
    gross_margin_basis_points: int | None = Field(
        serialization_alias="grossMarginBasisPoints"
    )
    cost_entries: list[FinanceCostEntryOut] = Field(
        serialization_alias="costEntries"
    )


class FinanceCostEntryCreate(BaseModel):
    """
    模块：新建财务成本条目请求
    用途：校验类别、名称、分金额与备注；忽略客户端 id/时间戳。
    对接：POST /api/finance/business-bids/{project_id}/cost-entries。
    二次开发：amountFen 仅接受 JSON 整数（StrictInt）；拒绝 bool/浮点/字符串强制转换。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    category: FinanceCostCategory
    name: str = Field(min_length=1, max_length=120)
    # StrictInt：仅 JSON 整数；1.0 / true / "1" 均 422，禁止浮点持久化
    amount_fen: StrictInt = Field(alias="amountFen", ge=1, le=999_999_999_999)
    remark: str = Field(default="", max_length=500)


class FinanceCostEntryUpdate(BaseModel):
    """
    模块：更新财务成本条目请求
    用途：至少一个可修改字段；服务端再做归属与边界校验。
    对接：PATCH /api/finance/business-bids/{project_id}/cost-entries/{entry_id}。
    二次开发：amountFen 与创建一致用 StrictInt；禁止改 id/workspace/project/user。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    category: FinanceCostCategory | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # StrictInt：仅 JSON 整数；1.0 / true / "1" 均 422
    amount_fen: StrictInt | None = Field(
        default=None, alias="amountFen", ge=1, le=999_999_999_999
    )
    remark: str | None = Field(default=None, max_length=500)


# ---------- P10J 财务个人成本变更记录 ----------


FinanceCostChangeAction = Literal["create", "update", "delete"]


class FinanceCostChangeEventOut(BaseModel):
    """
    模块：财务个人成本变更单条响应
    用途：固定投影动作、条目 ID 与发生时间；不含审计 ID/内部 action/金额。
    对接：GET /api/finance/cost-change-events → items[]。
    二次开发：字段集合为契约白名单，禁止附加项目/备注/操作者/工作空间。
    """

    model_config = ConfigDict(populate_by_name=True)

    action: FinanceCostChangeAction
    entry_id: str = Field(serialization_alias="entryId")
    occurred_at: datetime = Field(serialization_alias="occurredAt")


class FinanceCostChangeEventsOut(BaseModel):
    """
    模块：财务个人成本变更列表响应
    用途：包装 items 数组；顶层仅此键。
    对接：GET /api/finance/cost-change-events。
    二次开发：禁止分页游标、total、limit 或任何额外顶层字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[FinanceCostChangeEventOut]


# ---------- P10K 财务项目成本变更记录 ----------


FinanceProjectCostChangeActorScope = Literal["self", "other"]


class FinanceProjectCostChangeEventOut(BaseModel):
    """
    模块：财务项目成本变更单条响应
    用途：固定投影动作、条目 ID、本人/其他作用域与时间；不含事件 ID/成员身份/金额。
    对接：GET /api/finance/business-bids/{projectId}/cost-change-events → items[]。
    二次开发：字段集合为契约白名单，禁止附加项目/workspace/actor 原始 ID。
    """

    model_config = ConfigDict(populate_by_name=True)

    action: FinanceCostChangeAction
    entry_id: str = Field(serialization_alias="entryId")
    actor_scope: FinanceProjectCostChangeActorScope = Field(
        serialization_alias="actorScope"
    )
    occurred_at: datetime = Field(serialization_alias="occurredAt")


class FinanceProjectCostChangeEventsOut(BaseModel):
    """
    模块：财务项目成本变更列表响应
    用途：包装 items 数组；顶层仅此键。
    对接：GET /api/finance/business-bids/{projectId}/cost-change-events。
    二次开发：禁止分页游标、total、limit 或任何额外顶层字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[FinanceProjectCostChangeEventOut]


# ---------- P10D 人员资质素材卡 ----------


HrCredentialCategory = Literal[
    "professional", "safety", "performance", "other"
]


class HrCredentialCardSummaryOut(BaseModel):
    """
    模块：人员资质卡摘要响应
    用途：列表白名单字段；不含 remark 与创建人。
    对接：GET /api/hr/credential-cards。
    二次开发：字段集合为契约白名单，禁止附加证件/联系方式/附件。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    person_name: str = Field(serialization_alias="personName")
    category: HrCredentialCategory
    credential_name: str = Field(serialization_alias="credentialName")
    level: str = ""
    valid_until: date | None = Field(
        default=None, serialization_alias="validUntil"
    )
    is_active: bool = Field(serialization_alias="isActive")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class HrCredentialCardDetailOut(HrCredentialCardSummaryOut):
    """
    模块：人员资质卡详情响应
    用途：在摘要上追加 remark。
    对接：GET/POST/PATCH /api/hr/credential-cards*。
    二次开发：仍不得返回 createdBy/workspace/证件号等越界字段。
    """

    remark: str = ""


class HrCredentialCardListOut(BaseModel):
    """
    模块：人员资质卡列表响应
    用途：包装 items 数组。
    对接：GET /api/hr/credential-cards。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[HrCredentialCardSummaryOut]


class HrCredentialCardCreate(BaseModel):
    """
    模块：新建人员资质卡请求
    用途：校验显示名、类别、证书名与可选字段；拒绝额外敏感键。
    对接：POST /api/hr/credential-cards。
    二次开发：extra=forbid，禁止 idNumber/phone/attachment 等额外字段；
      isActive 仅接受 JSON 布尔（StrictBool），拒绝字符串/数值强制转换。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    person_name: str = Field(alias="personName", min_length=1, max_length=80)
    category: HrCredentialCategory
    credential_name: str = Field(
        alias="credentialName", min_length=1, max_length=120
    )
    level: str | None = Field(default="", max_length=80)
    valid_until: date | None = Field(default=None, alias="validUntil")
    remark: str = Field(default="", max_length=500)
    # StrictBool：仅 JSON true/false；"false"/0/1 均 422
    is_active: StrictBool = Field(default=True, alias="isActive")


class HrCredentialCardUpdate(BaseModel):
    """
    模块：更新人员资质卡请求
    用途：至少一个可修改字段；服务端再做归属与边界校验。
    对接：PATCH /api/hr/credential-cards/{cardId}。
    二次开发：extra=forbid；禁止改 id/workspace/user/时间戳；
      isActive 与创建一致用 StrictBool。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    person_name: str | None = Field(
        default=None, alias="personName", min_length=1, max_length=80
    )
    category: HrCredentialCategory | None = None
    credential_name: str | None = Field(
        default=None, alias="credentialName", min_length=1, max_length=120
    )
    level: str | None = Field(default=None, max_length=80)
    valid_until: date | None = Field(default=None, alias="validUntil")
    remark: str | None = Field(default=None, max_length=500)
    # StrictBool：仅 JSON true/false；"false"/0/1 均 422
    is_active: StrictBool | None = Field(default=None, alias="isActive")


# ---------- P10F 人力项目团队推荐快照 ----------


class HrTeamProjectSelectorItemOut(BaseModel):
    """
    模块：HR 技术标项目选择器项
    用途：仅暴露 id 与 name，供团队推荐绑定项目。
    对接：GET /api/hr/team-recommendations/projects。
    二次开发：禁止附加行业/状态/步骤/正文/文件等字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str


class HrTeamProjectSelectorListOut(BaseModel):
    """
    模块：HR 技术标项目选择器列表
    用途：包装 items。
    对接：GET /api/hr/team-recommendations/projects。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[HrTeamProjectSelectorItemOut]


class HrTeamRecommendationSummaryOut(BaseModel):
    """
    模块：团队推荐摘要
    用途：HR 列表展示项目维度的成员数与更新时间。
    对接：GET /api/hr/team-recommendations。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    project_name: str = Field(serialization_alias="projectName")
    member_count: int = Field(serialization_alias="memberCount")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class HrTeamRecommendationSummaryListOut(BaseModel):
    """
    模块：团队推荐摘要列表
    用途：包装 items。
    对接：GET /api/hr/team-recommendations。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[HrTeamRecommendationSummaryOut]


class HrTeamMemberSnapshotOut(BaseModel):
    """
    模块：HR 团队推荐成员快照
    用途：编辑详情中的有序成员；含 sourceCardId 供预选，不含 remark。
    对接：GET/PUT /api/hr/team-recommendations/{projectId}。
    """

    model_config = ConfigDict(populate_by_name=True)

    order: int
    person_name: str = Field(serialization_alias="personName")
    category: HrCredentialCategory
    credential_name: str = Field(serialization_alias="credentialName")
    level: str = ""
    valid_until: date | None = Field(default=None, serialization_alias="validUntil")
    source_card_id: str = Field(serialization_alias="sourceCardId")


class HrTeamRecommendationDetailOut(BaseModel):
    """
    模块：HR 团队推荐编辑详情
    用途：当前技术标项目的快照成员列表。
    对接：GET/PUT /api/hr/team-recommendations/{projectId}。
    二次开发：不得返回操作者、workspace、remark 或完整项目字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    project_name: str = Field(serialization_alias="projectName")
    members: list[HrTeamMemberSnapshotOut]
    updated_at: datetime = Field(serialization_alias="updatedAt")


class HrTeamRecommendationPut(BaseModel):
    """
    模块：保存团队推荐请求
    用途：仅接受有序 memberCardIds；extra=forbid。
    对接：PUT /api/hr/team-recommendations/{projectId}。
    二次开发：非字符串/空值/重复/长度由校验拒绝；有效卡校验在服务层完成。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    member_card_ids: list[Any] = Field(alias="memberCardIds")

    @field_validator("member_card_ids")
    @classmethod
    def _validate_member_card_ids(cls, value: Any) -> list[str]:
        """用途：严格校验有序卡 ID 列表；禁止类型强制与重复。"""
        if not isinstance(value, list):
            raise ValueError("invalid_member_card_ids")
        if len(value) > 30:
            raise ValueError("invalid_member_card_ids")
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if type(item) is not str:
                raise ValueError("invalid_member_card_ids")
            if item == "":
                raise ValueError("invalid_member_card_ids")
            if item in seen:
                raise ValueError("invalid_member_card_ids")
            seen.add(item)
            out.append(item)
        return out


class BidWriterTeamMemberOut(BaseModel):
    """
    模块：标书制作者团队推荐成员投影
    用途：最小展示字段；无来源卡 ID。
    对接：GET /api/projects/{projectId}/team-recommendation。
    """

    model_config = ConfigDict(populate_by_name=True)

    order: int
    person_name: str = Field(serialization_alias="personName")
    category: HrCredentialCategory
    credential_name: str = Field(serialization_alias="credentialName")
    level: str = ""
    valid_until: date | None = Field(default=None, serialization_alias="validUntil")


class BidWriterTeamRecommendationOut(BaseModel):
    """
    模块：标书制作者单项目团队推荐投影
    用途：empty/ready 固定结构；empty 时 updatedAt 为 null。
    对接：GET /api/projects/{projectId}/team-recommendation。
    二次开发：禁止返回 htr id、sourceCardId、remark、操作者或项目字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    data_state: Literal["empty", "ready"] = Field(serialization_alias="dataState")
    members: list[BidWriterTeamMemberOut]
    updated_at: datetime | None = Field(
        default=None, serialization_alias="updatedAt"
    )


# ---------- P10H 人员业绩素材卡 ----------


class HrPerformanceCardSummaryOut(BaseModel):
    """
    模块：人员业绩卡摘要响应
    用途：列表白名单字段；不含 performanceSummary 与 remark。
    对接：GET /api/hr/performance-cards。
    二次开发：字段集合为契约白名单，禁止附加证件/联系方式/附件/金额。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    person_name: str = Field(serialization_alias="personName")
    project_name: str = Field(serialization_alias="projectName")
    project_role: str = Field(default="", serialization_alias="projectRole")
    completed_year: int | None = Field(
        default=None, serialization_alias="completedYear"
    )
    is_active: bool = Field(serialization_alias="isActive")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class HrPerformanceCardDetailOut(HrPerformanceCardSummaryOut):
    """
    模块：人员业绩卡详情响应
    用途：在摘要上追加 performanceSummary 与 remark。
    对接：GET/POST/PATCH /api/hr/performance-cards*。
    二次开发：仍不得返回 createdBy/workspace/证件号等越界字段。
    """

    performance_summary: str = Field(serialization_alias="performanceSummary")
    remark: str = ""


class HrPerformanceCardListOut(BaseModel):
    """
    模块：人员业绩卡列表响应
    用途：包装 items 数组。
    对接：GET /api/hr/performance-cards。
    二次开发：仅摘要投影，禁止混入详情字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[HrPerformanceCardSummaryOut]


class HrPerformanceCardCreate(BaseModel):
    """
    模块：新建人员业绩卡请求
    用途：校验显示名、项目名、可选角色/年份与必填业绩摘要；拒绝额外敏感键。
    对接：POST /api/hr/performance-cards。
    二次开发：extra=forbid；projectRole 可省略默认空串但显式 null 须 422；
      completedYear 用 StrictInt(1900-2100) 且唯一允许 null；isActive 用 StrictBool。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    person_name: str = Field(alias="personName", min_length=1, max_length=80)
    project_name: str = Field(alias="projectName", min_length=1, max_length=120)
    # 可省略默认 ""；类型为 str（非 Optional）使显式 null 直接 422
    project_role: str = Field(default="", alias="projectRole", max_length=80)
    completed_year: StrictInt | None = Field(
        default=None, alias="completedYear", ge=1900, le=2100
    )
    performance_summary: str = Field(
        alias="performanceSummary", min_length=1, max_length=1000
    )
    remark: str = Field(default="", max_length=500)
    # StrictBool：仅 JSON true/false；"false"/0/1 均 422
    is_active: StrictBool = Field(default=True, alias="isActive")


class HrPerformanceCardUpdate(BaseModel):
    """
    模块：更新人员业绩卡请求
    用途：至少一个可修改字段；服务端再做归属与边界校验。
    对接：PATCH /api/hr/performance-cards/{cardId}。
    二次开发：extra=forbid；省略表示不修改；除 completedYear 外显式 null 须 422；
      completedYear 显式 null 用于清空；禁止改 id/workspace/user/时间戳。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    person_name: str | None = Field(
        default=None, alias="personName", min_length=1, max_length=80
    )
    project_name: str | None = Field(
        default=None, alias="projectName", min_length=1, max_length=120
    )
    project_role: str | None = Field(
        default=None, alias="projectRole", max_length=80
    )
    completed_year: StrictInt | None = Field(
        default=None, alias="completedYear", ge=1900, le=2100
    )
    performance_summary: str | None = Field(
        default=None,
        alias="performanceSummary",
        min_length=1,
        max_length=1000,
    )
    remark: str | None = Field(default=None, max_length=500)
    # StrictBool：仅 JSON true/false；"false"/0/1 均 422
    is_active: StrictBool | None = Field(default=None, alias="isActive")

    @field_validator(
        "person_name",
        "project_name",
        "project_role",
        "performance_summary",
        "remark",
        "is_active",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        """
        用途：省略字段表示不修改；显式 null 一律拒绝（completedYear 不在此列）。
        二次开发：未出现的键不会触发本校验器；禁止把 null 静默当「不改」。
        """
        if value is None:
            raise ValueError("字段不允许显式 null")
        return value


# ---------- P10E 投标人匿名合规预览 ----------


BidderComplianceDataState = Literal["ready", "empty"]


class BidderComplianceSummaryOut(BaseModel):
    """
    模块：投标人匿名合规汇总计数
    用途：仅输出条目总量与覆盖/未覆盖/豁免及整数基点。
    对接：GET /api/bidder/compliance-preview 的 summary。
    二次开发：禁止附加项目 ID/名称、矩阵行或原文。
    """

    model_config = ConfigDict(populate_by_name=True)

    total_items: int = Field(serialization_alias="totalItems")
    covered_items: int = Field(serialization_alias="coveredItems")
    uncovered_items: int = Field(serialization_alias="uncoveredItems")
    waived_items: int = Field(serialization_alias="waivedItems")
    coverage_basis_points: int | None = Field(
        serialization_alias="coverageBasisPoints"
    )


class BidderCompliancePreviewOut(BaseModel):
    """
    模块：投标人匿名合规预览响应
    用途：工作空间级只读聚合；dataState 仅 ready|empty。
    对接：GET /api/bidder/compliance-preview。
    二次开发：字段集合为契约白名单，不得扩展项目或矩阵细节。
    """

    model_config = ConfigDict(populate_by_name=True)

    data_state: BidderComplianceDataState = Field(serialization_alias="dataState")
    summary: BidderComplianceSummaryOut


# ---------- P10G 投标人项目级合规统计预览 ----------


class BidderProjectComplianceSelectorItemOut(BaseModel):
    """
    模块：投标人项目合规选择器项
    用途：仅暴露技术标项目 id 与 name。
    对接：GET /api/bidder/project-compliance/projects。
    二次开发：禁止附加行业/状态/步骤/矩阵/文件等字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str


class BidderProjectComplianceSelectorListOut(BaseModel):
    """
    模块：投标人项目合规选择器列表
    用途：包装当前空间技术标 id/name 白名单。
    对接：GET /api/bidder/project-compliance/projects。
    二次开发：items 字段集合固定，不得扩展分页或筛选元数据。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[BidderProjectComplianceSelectorItemOut]


class BidderProjectComplianceDetailOut(BaseModel):
    """
    模块：投标人单项目合规统计响应
    用途：仅输出 dataState 与五项 summary；空矩阵为 empty。
    对接：GET /api/bidder/project-compliance/{projectId}。
    二次开发：禁止回显 projectId/name、矩阵行、sourceKey、章节/大纲、人员/财务。
    """

    model_config = ConfigDict(populate_by_name=True)

    data_state: BidderComplianceDataState = Field(serialization_alias="dataState")
    summary: BidderComplianceSummaryOut


# ---------- P10I 人员资质到期提示 ----------


# 内部分类可含 valid（仅计数）；关注列表输出禁止 valid
HrCredentialExpiryState = Literal[
    "expired", "expiring_soon", "valid", "missing_expiry"
]
HrCredentialExpiryAttentionState = Literal[
    "expired", "expiring_soon", "missing_expiry"
]


class HrCredentialExpiryAttentionItemOut(BaseModel):
    """
    模块：人员资质到期关注项
    用途：仅投影契约白名单字段；不含 remark、时间戳、工作空间、创建人。
    对接：GET /api/hr/credential-expiry 的 attentionItems。
    二次开发：
      - 字段集合固定；禁止附加证件号/附件/路径/外链或未列字段
      - state 仅 expired/expiring_soon/missing_expiry，拒绝 valid
    """

    model_config = ConfigDict(populate_by_name=True)

    card_id: str = Field(serialization_alias="cardId")
    person_name: str = Field(serialization_alias="personName")
    category: HrCredentialCategory
    credential_name: str = Field(serialization_alias="credentialName")
    level: str = ""
    valid_until: date | None = Field(
        default=None, serialization_alias="validUntil"
    )
    state: HrCredentialExpiryAttentionState
    days_remaining: int | None = Field(
        default=None, serialization_alias="daysRemaining"
    )


class HrCredentialExpiryOut(BaseModel):
    """
    模块：人员资质到期提示响应
    用途：服务端 UTC 日期、固定 90 天窗口、固定计数与关注列表。
    对接：GET /api/hr/credential-expiry。
    二次开发：禁止接收客户端 asOf/window；valid 只计数不进 attentionItems；
      停用卡只计入 inactiveExcludedCount。
    """

    model_config = ConfigDict(populate_by_name=True)

    as_of_date: date = Field(serialization_alias="asOfDate")
    window_days: int = Field(serialization_alias="windowDays")
    active_total_count: int = Field(serialization_alias="activeTotalCount")
    expired_count: int = Field(serialization_alias="expiredCount")
    expiring_soon_count: int = Field(serialization_alias="expiringSoonCount")
    valid_count: int = Field(serialization_alias="validCount")
    missing_expiry_count: int = Field(serialization_alias="missingExpiryCount")
    inactive_excluded_count: int = Field(
        serialization_alias="inactiveExcludedCount"
    )
    attention_items: list[HrCredentialExpiryAttentionItemOut] = Field(
        serialization_alias="attentionItems"
    )


# ---------- M3-D 融合写入持久恢复批次 ----------


class ContentFuseApplicationCreate(BaseModel):
    """
    模块：M3-D 原子确认请求
    用途：仅接受 camelCase 的 taskId、suggestionIds、expectedStateVersion；
      拒绝客户端正文/base/action 等伪造键。
    对接：POST /api/projects/{projectId}/content-fuse-applications。
    二次开发：
      - extra=forbid；禁止 populate_by_name，snake_case 必须 422；
      - expectedStateVersion 强制合法 esv_ 格式；
      - 建议正文必须仅来自服务端成功 content_fuse 任务结果。
    """

    # 故意不设 populate_by_name：只接受 JSON 键 taskId / suggestionIds / expectedStateVersion
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(alias="taskId", min_length=1, max_length=64)
    suggestion_ids: list[str] = Field(alias="suggestionIds")
    expected_state_version: str = Field(
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
        min_length=1,
    )

    @field_validator("task_id")
    @classmethod
    def _task_id_nonblank(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("taskId 不能为空")
        return text

    @field_validator("suggestion_ids")
    @classmethod
    def _suggestion_ids_shape(cls, value: list[str]) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValueError("suggestionIds 须为 1–5 个非空字符串")
        if len(value) > 5:
            raise ValueError("suggestionIds 最多 5 个")
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if type(item) is not str:
                raise ValueError("suggestionIds 每项必须是字符串")
            sid = item.strip()
            if not sid:
                raise ValueError("suggestionIds 含空 ID")
            if len(sid) > 64:
                raise ValueError("suggestionIds 单项过长")
            if sid in seen:
                raise ValueError("suggestionIds 不可重复")
            seen.add(sid)
            out.append(sid)
        return out


class ContentFuseApplicationConsume(BaseModel):
    """
    模块：M3-D 一次性恢复请求
    用途：仅接受 camelCase expectedStateVersion；无 body 其它键。
    对接：POST .../content-fuse-applications/{batchId}/consume。
    二次开发：extra=forbid；禁止 populate_by_name；snake_case/缺失/非法/额外键 422。
    """

    model_config = ConfigDict(extra="forbid")

    expected_state_version: str = Field(
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
        min_length=1,
    )


class ContentFuseApplicationCreateOut(BaseModel):
    """
    模块：M3-D 原子确认成功响应
    用途：返回 batchId/appliedChapterCount/createdAt/stateVersion。
    对接：POST /api/projects/{projectId}/content-fuse-applications。
    二次开发：禁止回显 taskId、正文、快照或冲突章节细节。
    """

    model_config = ConfigDict(populate_by_name=True)

    batch_id: str = Field(serialization_alias="batchId")
    applied_chapter_count: int = Field(serialization_alias="appliedChapterCount")
    created_at: datetime = Field(serialization_alias="createdAt")
    state_version: str = Field(
        serialization_alias="stateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )


class ContentFuseApplicationListItemOut(BaseModel):
    """
    模块：M3-D 批次列表项
    用途：最小投影 batchId/chapterCount/state/createdAt/consumedAt。
    对接：GET /api/projects/{projectId}/content-fuse-applications。
    二次开发：禁止返回 task/suggestion/chapter/正文/标题。
    """

    model_config = ConfigDict(populate_by_name=True)

    batch_id: str = Field(serialization_alias="batchId")
    chapter_count: int = Field(serialization_alias="chapterCount")
    state: Literal["active", "consumed"]
    created_at: datetime = Field(serialization_alias="createdAt")
    consumed_at: datetime | None = Field(
        default=None, serialization_alias="consumedAt"
    )


class ContentFuseApplicationListOut(BaseModel):
    """
    模块：M3-D 批次列表响应
    用途：固定最近 20 条包装为 items。
    对接：GET /api/projects/{projectId}/content-fuse-applications。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[ContentFuseApplicationListItemOut]


class ContentFuseApplicationConsumeOut(BaseModel):
    """
    模块：M3-D 一次性恢复响应
    用途：restoredChapterCount/skippedChapterCount/consumedAt/stateVersion。
    对接：POST .../content-fuse-applications/{batchId}/consume。
    二次开发：禁止回显正文、快照、路径或批次详情；零恢复时版本等于操作前。
    """

    model_config = ConfigDict(populate_by_name=True)

    restored_chapter_count: int = Field(serialization_alias="restoredChapterCount")
    skipped_chapter_count: int = Field(serialization_alias="skippedChapterCount")
    consumed_at: datetime = Field(serialization_alias="consumedAt")
    state_version: str = Field(
        serialization_alias="stateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )


class EditorStateRevisionMetaOut(BaseModel):
    """
    模块：P12C-C1 修订历史元数据
    用途：列表项字段，不含 snapshot 正文。
    对接：GET /api/projects/{projectId}/editor-state-revisions。
    二次开发：禁止附加 projectId/正文/路径/用户/任务字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    revision_id: str = Field(serialization_alias="revisionId")
    state_version: str = Field(serialization_alias="stateVersion")
    snapshot_bytes: int = Field(serialization_alias="snapshotBytes")
    source_kind: str = Field(serialization_alias="sourceKind")
    created_at: datetime = Field(serialization_alias="createdAt")


class EditorStateRevisionListOut(BaseModel):
    """
    模块：P12C-C1 修订历史列表
    用途：固定最近 10 条元数据，顶层仅 items。
    对接：GET /api/projects/{projectId}/editor-state-revisions。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[EditorStateRevisionMetaOut]


class EditorStateRevisionDetailOut(BaseModel):
    """
    模块：P12C-C1 修订历史详情
    用途：元数据 + 已校验的规范 snapshot 对象。
    对接：GET .../editor-state-revisions/{revisionId}。
    二次开发：snapshot 必须服务端重验键集/字节/版本后返回。
    """

    model_config = ConfigDict(populate_by_name=True)

    revision_id: str = Field(serialization_alias="revisionId")
    state_version: str = Field(serialization_alias="stateVersion")
    snapshot_bytes: int = Field(serialization_alias="snapshotBytes")
    source_kind: str = Field(serialization_alias="sourceKind")
    created_at: datetime = Field(serialization_alias="createdAt")
    snapshot: dict[str, Any]


class EditorStateRevisionRestore(BaseModel):
    """
    模块：P12C-C2 修订受限恢复请求
    用途：仅接受 camelCase expectedStateVersion；无 body 其它键。
    对接：POST .../editor-state-revisions/{revisionId}/restore。
    二次开发：extra=forbid；禁止 populate_by_name；snake_case/缺失/非法/额外键 422。
    """

    model_config = ConfigDict(extra="forbid")

    expected_state_version: str = Field(
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
        min_length=1,
    )


class EditorStateRevisionRestoreOut(BaseModel):
    """
    模块：P12C-C2 修订受限恢复响应
    用途：仅 safetyCheckpointId/stateVersion/restoredAt。
    对接：POST .../editor-state-revisions/{revisionId}/restore。
    二次开发：禁止回显 revision ID、新 revision 行 ID、正文、原来源或路径。
    """

    model_config = ConfigDict(populate_by_name=True)

    safety_checkpoint_id: str = Field(serialization_alias="safetyCheckpointId")
    state_version: str = Field(
        serialization_alias="stateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )
    restored_at: str = Field(serialization_alias="restoredAt")


# P12D-A：修订与当前状态差异摘要（仅六项计数 + 13 键字段名；无正文/ID/版本）
EditorStateCanonicalFieldName = Literal[
    "outline",
    "chapters",
    "facts",
    "mode",
    "analysis",
    "responseMatrix",
    "guidance",
    "parsedMarkdown",
    "businessQualify",
    "businessToc",
    "businessQuote",
    "businessCommit",
    "analysisOverview",
]


class EditorStateRevisionComparisonSummaryOut(BaseModel):
    """
    模块：P12D-A 差异摘要两侧六键计数
    用途：有界节点/数组统计；禁止附加字段值或正文。
    对接：GET .../editor-state-revisions/{revisionId}/comparison。
    二次开发：精确六键；计数非负整数；hasParsedMarkdown 仅布尔。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    outline_node_count: int = Field(
        serialization_alias="outlineNodeCount", ge=0
    )
    chapter_count: int = Field(serialization_alias="chapterCount", ge=0)
    fact_count: int = Field(serialization_alias="factCount", ge=0)
    response_matrix_row_count: int = Field(
        serialization_alias="responseMatrixRowCount", ge=0
    )
    business_entry_total: int = Field(
        serialization_alias="businessEntryTotal", ge=0
    )
    has_parsed_markdown: bool = Field(serialization_alias="hasParsedMarkdown")


class EditorStateRevisionComparisonOut(BaseModel):
    """
    模块：P12D-A 修订与当前状态差异摘要响应
    用途：仅 sameState/changedFields/currentSummary/targetSummary。
    对接：GET .../editor-state-revisions/{revisionId}/comparison。
    二次开发：禁止 ID、版本、来源、时间、snapshot 或字段原值。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    same_state: bool = Field(serialization_alias="sameState")
    changed_fields: list[EditorStateCanonicalFieldName] = Field(
        serialization_alias="changedFields"
    )
    current_summary: EditorStateRevisionComparisonSummaryOut = Field(
        serialization_alias="currentSummary"
    )
    target_summary: EditorStateRevisionComparisonSummaryOut = Field(
        serialization_alias="targetSummary"
    )


# P12E-A：修订与当前状态章节正文差异（有界 hunks；无 ID/版本/路径）
EditorStateBodyDiffKind = Literal["added", "removed", "changed"]
EditorStateBodyDiffOp = Literal["equal", "delete", "insert"]


class EditorStateRevisionBodyDiffHunkOut(BaseModel):
    """
    模块：P12E-A 正文差异单行片段
    用途：仅 op/text 二键；op 限定 equal|delete|insert。
    对接：GET .../editor-state-revisions/{revisionId}/body-diff items[].hunks。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    op: EditorStateBodyDiffOp
    text: str


class EditorStateRevisionBodyDiffItemOut(BaseModel):
    """
    模块：P12E-A 正文差异单章项
    用途：仅 ordinal/kind/beforeTitle/afterTitle/hunks 五键。
    对接：GET .../editor-state-revisions/{revisionId}/body-diff items。
    二次开发：禁止 chapterId/revisionId/stateVersion 等内部标识。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ordinal: int = Field(ge=1)
    kind: EditorStateBodyDiffKind
    before_title: str = Field(serialization_alias="beforeTitle")
    after_title: str = Field(serialization_alias="afterTitle")
    hunks: list[EditorStateRevisionBodyDiffHunkOut]


class EditorStateRevisionBodyDiffOut(BaseModel):
    """
    模块：P12E-A 修订与当前状态章节正文差异响应
    用途：仅 sameBody/changedChapterCount/currentChapterCount/
      targetChapterCount/truncated/items 六键。
    对接：GET .../editor-state-revisions/{revisionId}/body-diff。
    二次开发：禁止 ID、版本、来源、时间、原始快照、异常原文。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    same_body: bool = Field(serialization_alias="sameBody")
    changed_chapter_count: int = Field(
        serialization_alias="changedChapterCount", ge=0
    )
    current_chapter_count: int = Field(
        serialization_alias="currentChapterCount", ge=0
    )
    target_chapter_count: int = Field(
        serialization_alias="targetChapterCount", ge=0
    )
    truncated: bool
    items: list[EditorStateRevisionBodyDiffItemOut]


class EditorStateCheckpointCreate(BaseModel):
    """
    模块：P12A 手动检查点创建请求
    用途：仅接受精确空对象 {}；拒绝任何客户端快照/名称/版本投稿。
    对接：POST /api/projects/{projectId}/editor-state-checkpoints。
    二次开发：extra=forbid；禁止 populate_by_name 扩展字段。
    """

    model_config = ConfigDict(extra="forbid")


class EditorStateCheckpointMetaOut(BaseModel):
    """
    模块：P12A 检查点元数据
    用途：创建响应与列表项共用字段，不含 snapshot 正文。
    对接：POST/GET .../editor-state-checkpoints。
    二次开发：禁止附加 projectId/正文/路径/用户字段。
    """

    model_config = ConfigDict(populate_by_name=True)

    checkpoint_id: str = Field(serialization_alias="checkpointId")
    state_version: str = Field(serialization_alias="stateVersion")
    snapshot_bytes: int = Field(serialization_alias="snapshotBytes")
    outline_node_count: int = Field(serialization_alias="outlineNodeCount")
    chapter_count: int = Field(serialization_alias="chapterCount")
    created_at: datetime = Field(serialization_alias="createdAt")


class EditorStateCheckpointListOut(BaseModel):
    """
    模块：P12A 检查点列表
    用途：固定最近 20 条元数据，顶层仅 items。
    对接：GET /api/projects/{projectId}/editor-state-checkpoints。
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[EditorStateCheckpointMetaOut]


class EditorStateCheckpointDetailOut(BaseModel):
    """
    模块：P12A 检查点详情
    用途：元数据 + 已校验的规范 snapshot 对象。
    对接：GET .../editor-state-checkpoints/{checkpointId}。
    二次开发：snapshot 必须服务端重验键集/字节/版本后返回。
    """

    model_config = ConfigDict(populate_by_name=True)

    checkpoint_id: str = Field(serialization_alias="checkpointId")
    state_version: str = Field(serialization_alias="stateVersion")
    snapshot_bytes: int = Field(serialization_alias="snapshotBytes")
    outline_node_count: int = Field(serialization_alias="outlineNodeCount")
    chapter_count: int = Field(serialization_alias="chapterCount")
    created_at: datetime = Field(serialization_alias="createdAt")
    snapshot: dict[str, Any]


class EditorStateCheckpointRestore(BaseModel):
    """
    模块：P12B-D1 检查点安全恢复请求
    用途：仅接受 camelCase expectedStateVersion；无 body 其它键。
    对接：POST .../editor-state-checkpoints/{checkpointId}/restore。
    二次开发：extra=forbid；禁止 populate_by_name；snake_case/缺失/非法/额外键 422。
    """

    model_config = ConfigDict(extra="forbid")

    expected_state_version: str = Field(
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
        min_length=1,
    )


class EditorStateCheckpointRestoreOut(BaseModel):
    """
    模块：P12B-D1 检查点安全恢复响应
    用途：仅 restoredCheckpointId/safetyCheckpointId/stateVersion/restoredAt。
    对接：POST .../editor-state-checkpoints/{checkpointId}/restore。
    二次开发：stateVersion 必须等于目标检查点已验证版本；restoredAt 对齐本轮 editor-state updatedAt；
      禁止回显正文、快照、标题、矩阵、路径或异常细节。
    """

    model_config = ConfigDict(populate_by_name=True)

    restored_checkpoint_id: str = Field(serialization_alias="restoredCheckpointId")
    safety_checkpoint_id: str = Field(serialization_alias="safetyCheckpointId")
    state_version: str = Field(
        serialization_alias="stateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
    )
    restored_at: str = Field(serialization_alias="restoredAt")
