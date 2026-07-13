"""
模块：数据模型包
用途：集中导出 ORM 实体，保证 main 建表与 service import 路径统一。
对接：from app.models import Workspace, Project
二次开发：新实体在 entities 定义后在此 __all__ 导出。
"""

from app.models.entities import (
    AuthAuditEventRow,
    AuthSessionRow,
    BidOpportunityRow,
    BidSourceHitRow,
    BidSourceSyncRunRow,
    BidTemplateRow,
    BidWatchPlanRow,
    KbChunkRow,
    KbDocumentRow,
    KbFolderRow,
    KnowledgeCardRow,
    LocalUserRow,
    Project,
    ProjectEditorStateRow,
    ProjectFileRow,
    ProjectTaskRow,
    ResourceRow,
    ResourceSyncItemRow,
    ResourceSyncRunRow,
    ResourceSyncSourceRow,
    SemanticChunkEmbeddingRow,
    SemanticEmbeddingIndexRow,
    Workspace,
    WorkspaceMemberRow,
    WorkspaceSettingsRow,
)

__all__ = [
    "Workspace",
    "Project",
    "WorkspaceSettingsRow",
    "ProjectEditorStateRow",
    "ProjectFileRow",
    "ProjectTaskRow",
    "KbFolderRow",
    "KbDocumentRow",
    "KbChunkRow",
    "SemanticEmbeddingIndexRow",
    "SemanticChunkEmbeddingRow",
    "KnowledgeCardRow",
    "BidOpportunityRow",
    "BidWatchPlanRow",
    "BidSourceSyncRunRow",
    "BidSourceHitRow",
    "BidTemplateRow",
    "ResourceRow",
    "ResourceSyncSourceRow",
    "ResourceSyncRunRow",
    "ResourceSyncItemRow",
    "LocalUserRow",
    "WorkspaceMemberRow",
    "AuthSessionRow",
    "AuthAuditEventRow",
]
