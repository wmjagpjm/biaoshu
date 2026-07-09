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
    export as export_api,
    files,
    health,
    llm,
    projects,
    revise,
    settings as settings_api,
    tasks,
)
from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
# 导入实体以注册 Base.metadata（create_all 依赖）
from app.models import (  # noqa: F401
    Project,
    ProjectEditorStateRow,
    ProjectFileRow,
    ProjectTaskRow,
    Workspace,
    WorkspaceSettingsRow,
)
from app.services.project_service import ensure_default_workspace


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    用途：应用生命周期钩子。
    启动阶段：create_all 建表 + 确保默认 workspace 存在。
    关闭阶段：当前无资源释放；后续可关连接池/停 Worker。
    """
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    db = SessionLocal()
    try:
        ensure_default_workspace(db, settings)
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
    app.include_router(projects.router, prefix="/api")
    app.include_router(settings_api.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")
    app.include_router(revise.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(export_api.router, prefix="/api")
    return app


# uvicorn 入口对象
app = create_app()
