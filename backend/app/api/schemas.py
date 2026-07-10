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

from datetime import datetime
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
    用途：编辑器整包状态（技术标 outline/chapters + 商务标 business*）。
    对接：useTechnicalPlanEditors / useProjectGuidance / useBusinessBidWorkspace
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    outline: list | dict | None = None
    chapters: list | dict | None = None
    facts: list | dict | None = None
    mode: str = "ALIGNED"
    analysis_overview: str | None = Field(default=None, serialization_alias="analysisOverview")
    analysis: dict | None = None
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
    """用途：PUT 部分字段；未传的键不覆盖。"""

    model_config = ConfigDict(populate_by_name=True)

    outline: list | dict | None = None
    chapters: list | dict | None = None
    facts: list | dict | None = None
    mode: str | None = None
    analysis_overview: str | None = Field(default=None, alias="analysisOverview")
    analysis: dict | None = None
    guidance: dict | None = None
    parsed_markdown: str | None = Field(default=None, alias="parsedMarkdown")
    business_qualify: list | None = Field(default=None, alias="businessQualify")
    business_toc: list | None = Field(default=None, alias="businessToc")
    business_quote: dict | None = Field(default=None, alias="businessQuote")
    business_commit: list | None = Field(default=None, alias="businessCommit")
