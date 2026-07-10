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

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
    用途：技术标（及后续可扩展）项目列表实体，工作区入口。
    字段（Python snake_case / API camelCase）：
      - id → id
      - workspace_id → workspaceId
      - name → name
      - industry → industry
      - status → status（draft|analyzing|writing|reviewing|exported）
      - updated_at → updatedAt
      - technical_plan_step → technicalPlanStep（1–6）
      - word_count → wordCount
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

    workspace: Mapped["Workspace"] = relationship(back_populates="projects")


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
    # 默认导出模板 JSON（对齐前端 ExportFormatConfig，明文）
    export_format_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ProjectEditorStateRow(Base):
    """
    用途：技术标工作区最小持久化（大纲/章节/事实/分析概述/guidance）。
    对接：GET|PUT /api/projects/{id}/editor-state；前端 useTechnicalPlanEditors / guidance。
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
    guidance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 轻量解析后的招标文件 Markdown（document 步）
    parsed_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ProjectFileRow(Base):
    """
    用途：项目上传文件元数据；实体文件在 uploads/{project_id}/ 目录。
    对接：POST /api/projects/{id}/files
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
    用途：知识库文本分块，供关键词检索与生成注入。
    对接：knowledge_service.search_chunks；task_service RAG 注入
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
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
