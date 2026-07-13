"""
模块：P10A 本机管理员交互式引导
用途：创建首个本机用户，并使其成为默认工作空间所有者（bid_writer）。
对接：auth_service.bootstrap_local_admin；Settings / 数据库。
二次开发：
  - 口令仅通过 getpass 交互读取，禁止命令行参数、环境变量或写入 .env
  - 禁止网络、邮件、第三方身份；失败时不回显口令
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

# 允许从 backend 根或仓库根启动
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, ensure_schema_columns  # noqa: E402
from app.models import (  # noqa: E402, F401
    AuthAuditEventRow,
    AuthSessionRow,
    LocalUserRow,
    Workspace,
    WorkspaceMemberRow,
)
from app.services import auth_service  # noqa: E402
from app.services.project_service import ensure_default_workspace  # noqa: E402


def main() -> int:
    """用途：CLI 入口；成功 0，受控失败 2，意外错误 1。"""
    print("标书本机管理员引导（口令不会回显，也不会写入配置文件）")
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()

    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            print("错误：管理员已初始化，拒绝重复引导。", file=sys.stderr)
            return 2

        ensure_default_workspace(db, settings)

        username = input("管理员用户名: ").strip()
        if not username:
            print("错误：用户名不能为空。", file=sys.stderr)
            return 2

        password = getpass.getpass("管理员口令: ")
        confirm = getpass.getpass("再次输入口令: ")
        if password != confirm:
            print("错误：两次口令不一致。", file=sys.stderr)
            return 2

        principal = auth_service.bootstrap_local_admin(
            db,
            settings,
            username=username,
            password=password,
            role=auth_service.ROLE_BID_WRITER,
        )
        # 立即丢弃口令变量引用（尽力）
        del password
        del confirm

        print("初始化成功。")
        print(f"  用户: {principal.username}")
        print(f"  用户ID: {principal.user_id}")
        print(f"  默认工作空间: {principal.active_workspace_id}")
        print("请将 AUTH_MODE=required 写入部署环境后重启服务（勿把口令写入 .env）。")
        return 0
    except auth_service.AuthError as exc:
        print(f"错误：{exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI 顶层
        print(f"错误：引导失败（{type(exc).__name__}）", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
