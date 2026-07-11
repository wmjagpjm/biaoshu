"""
模块：受控资源同步管理员命令
用途：在本机管理员环境初始化资源表并执行服务端预配置的签名清单同步，输出脱敏状态和计数。
对接：backend/.env 的 RESOURCE_SYNC_*；resource_sync_service；Windows 任务计划程序或手动 python scripts/sync_resources.py。
二次开发：不得改为浏览器调用、不得接受 URL/Token 命令行参数、不得输出清单 URL/公钥/正文或从本命令写入用户资源。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从 backend 目录执行 python scripts/sync_resources.py，且不依赖全局 PYTHONPATH。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine, ensure_schema_columns
from app.models import ResourceRow  # noqa: F401 仅确保所有同步相关实体注册到 Base.metadata
from app.services.resource_service import ensure_system_resources
from app.services.resource_sync_service import (
    ResourceSyncError,
    configured_sources,
    sync_source,
)


def _prepare_database() -> None:
    """
    用途：复用应用启动期的建表与轻量 SQLite 兼容步骤，使离线命令可在未启动 Web 服务时安全运行。
    对接：Base.metadata；ensure_schema_columns；ensure_system_resources。
    二次开发：正式迁移到 Alembic 后，应与应用启动共享同一迁移入口，避免命令与 Web 服务模式漂移。
    """
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()
    db = SessionLocal()
    try:
        ensure_system_resources(db)
    finally:
        db.close()


def main() -> int:
    """
    用途：逐个执行配置来源，单个来源失败不阻塞其余来源，并以退出码向本机计划任务反馈结果。
    对接：命令行入口；RESOURCE_SYNC_SOURCES；resource_sync_service.sync_source。
    二次开发：需要定时执行时由操作系统任务计划程序调用；不得在 FastAPI 请求、前端或无鉴权定时器中直接复用。
    """
    settings = get_settings()
    try:
        sources = configured_sources(settings)
    except ResourceSyncError as exc:
        print(f"同步来源配置错误：{exc}")
        return 2
    if not sources:
        print("未配置受控资源同步来源，未发起网络请求。")
        return 0

    _prepare_database()
    failures = 0
    allowed_hosts = settings.resource_sync_allowed_host_set()
    db = SessionLocal()
    try:
        for source in sources:
            try:
                result = sync_source(
                    db,
                    source,
                    allowed_hosts=allowed_hosts,
                    max_bytes=settings.resource_sync_max_bytes,
                    timeout_seconds=settings.resource_sync_timeout_seconds,
                )
            except ResourceSyncError as exc:
                failures += 1
                print(f"来源 {source.id} 同步失败：{exc}")
                continue
            print(
                f"来源 {result.source_id} 同步成功："
                f"新增 {result.created}，更新 {result.updated}，跳过 {result.skipped}。"
            )
    finally:
        db.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
