"""
模块：FastAPI 应用入口
用途：
  1. 创建 FastAPI 实例并注册 CORS、路由前缀 /api
  2. 进程启动时建表、seed 默认个人版 workspace
  3. 作为 uvicorn 加载目标
对接：
  - 启动：uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
  - 前端：Vite 代理 /api → 本服务，或 VITE_API_BASE_URL=http://127.0.0.1:8000/api
二次开发：
  - 新增业务路由：在 create_app 里 include_router，统一挂 /api 前缀
  - 长任务 / SSE / Worker 初始化可放进 lifespan
  - 勿在此写业务逻辑，只做组装
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth as auth_api,
    bidder as bidder_api,
    cards as cards_api,
    compliance as compliance_api,
    content_fuse_applications as content_fuse_applications_api,
    editor_state_checkpoints as editor_state_checkpoints_api,
    editor_state_revisions as editor_state_revisions_api,
    export as export_api,
    files,
    finance as finance_api,
    health,
    hr as hr_api,
    knowledge as knowledge_api,
    llm,
    opportunities,
    opportunity_watch as opportunity_watch_api,
    parse_callback,
    projects,
    resources,
    revise,
    settings as settings_api,
    tasks,
    templates as templates_api,
)
from app.api.auth_middleware import AuthMiddleware
from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine, ensure_schema_columns
# 导入实体以注册 Base.metadata（create_all 依赖）
from app.models import (  # noqa: F401
    AuthAuditEventRow,
    AuthSessionRow,
    BidOpportunityRow,
    BidSourceHitRow,
    BidSourceSyncRunRow,
    BidTemplateRow,
    BidWatchPlanRow,
    ContentFuseApplicationBatchRow,
    EditorStateCheckpointRow,
    FinanceCostEntryRow,
    FinanceProjectCostChangeEventRow,
    HrCredentialCardRow,
    HrPerformanceCardRow,
    HrTeamRecommendationMemberRow,
    HrTeamRecommendationRow,
    KbChunkRow,
    KbDocumentRow,
    KbFolderRow,
    KnowledgeCardRow,
    LocalParserCallbackTicketRow,
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
from app.services.project_service import ensure_default_workspace
from app.services.opportunity_service import ensure_sample_opportunities
from app.services.opportunity_watch_service import mark_interrupted_watch_runs
from app.services.knowledge_service import mark_interrupted_semantic_indexes
from app.services.resource_service import ensure_system_resources
from app.services.task_service import fail_interrupted_tasks


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    用途：应用生命周期钩子。
    启动阶段：建表 + seed workspace + 中断残留任务/语义索引。
    """
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()
    settings = get_settings()
    db = SessionLocal()
    try:
        ensure_default_workspace(db, settings)
        if settings.seed_sample_opportunities:
            ensure_sample_opportunities(db, settings.default_workspace_id)
        ensure_system_resources(db)
        mark_interrupted_watch_runs(db)
        mark_interrupted_semantic_indexes(db)
        fail_interrupted_tasks(db)
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    """
    用途：工厂函数，组装中间件与路由，便于测试注入。
    返回：配置完成的 FastAPI 实例。
    """
    settings = get_settings()
    app = FastAPI(
        title="标书后端",
        description="biaoshu Web 自托管 API（个人版起步）",
        version="0.1.0",
        lifespan=lifespan,
    )
    # 先注册鉴权中间件，再注册 CORS（后添加的 CORS 处于最外层，便于预检）
    app.add_middleware(AuthMiddleware)
    # 开发期允许 Vite 源；生产请在 .env 收紧 CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 路由前缀 /api：与前端 apiFetch 的 base（默认 /api）拼接后完整路径一致
    app.include_router(health.router, prefix="/api")
    app.include_router(auth_api.router, prefix="/api")
    app.include_router(finance_api.router, prefix="/api")
    app.include_router(hr_api.router, prefix="/api")
    app.include_router(bidder_api.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(content_fuse_applications_api.router, prefix="/api")
    app.include_router(editor_state_checkpoints_api.router, prefix="/api")
    app.include_router(editor_state_revisions_api.router, prefix="/api")
    app.include_router(settings_api.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")
    app.include_router(revise.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(export_api.router, prefix="/api")
    app.include_router(parse_callback.router, prefix="/api")
    app.include_router(parse_callback.public_router, prefix="/api")
    app.include_router(knowledge_api.router, prefix="/api")
    app.include_router(cards_api.router, prefix="/api")
    app.include_router(compliance_api.router, prefix="/api")
    app.include_router(opportunities.router, prefix="/api")
    app.include_router(opportunity_watch_api.router, prefix="/api")
    app.include_router(resources.router, prefix="/api")
    app.include_router(templates_api.router, prefix="/api")
    return app


# uvicorn 入口对象
app = create_app()
