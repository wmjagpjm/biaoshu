"""
模块：可插拔文档解析引擎调度
用途：按名称解析并调度 ParseEngine；生产默认仅 lightweight，测试可受控注入 fake。
对接：task_service._run_parse；parse_service.parse_file_to_markdown（lightweight 内部实现）。
二次开发：
  - 禁止在本模块引入 subprocess / shell / 网络 / 外部二进制 / 任意路径读取。
  - 生产注册表不得默认包含 fake；真实 MinerU/Docling 须外置进程 + parse-callback，不得内嵌。
  - parseStrategy=local/ask 当前不驱动本调度器，勿在此假称已接线。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from app.services import parse_service

# 默认生产引擎名
DEFAULT_ENGINE_NAME = "lightweight"


class EngineUnavailableError(ValueError):
    """
    用途：引擎名称非法、未注册或明确不可用时抛出。
    对接：task_service 捕获后任务 failed；错误文案须含「解析引擎不可用」。
    二次开发：禁止静默回退到其它引擎，避免用户误以为使用了指定引擎。
    """


@runtime_checkable
class ParseEngine(Protocol):
    """
    用途：解析引擎最小协议。
    对接：register_engine / get_engine / parse_with_engine。
    二次开发：parse 仅接受已解析的本机 Path 与展示用文件名，不得自行打开网络或外部命令。
    """

    name: str

    def parse(self, path: Path, original_name: str) -> str:
        """用途：将本地文件解析为 Markdown 全文。"""
        ...


class LightweightParseEngine:
    """
    用途：内置轻量引擎；委托现有 parse_service，保持同输入同输出。
    对接：默认注册表；task payload engine 缺省。
    二次开发：勿在此扩展 MinerU/Docling；复杂版式仍走外置 callback。
    """

    name = DEFAULT_ENGINE_NAME

    def parse(self, path: Path, original_name: str) -> str:
        return parse_service.parse_file_to_markdown(path, original_name)


# 进程内注册表；模块加载后仅含 lightweight（生产路径）
_REGISTRY: dict[str, ParseEngine] = {}


def _ensure_defaults() -> None:
    """用途：保证默认引擎始终可用。"""
    if DEFAULT_ENGINE_NAME not in _REGISTRY:
        _REGISTRY[DEFAULT_ENGINE_NAME] = LightweightParseEngine()


def register_engine(engine: ParseEngine, *, overwrite: bool = False) -> None:
    """
    用途：注册引擎；测试可注入 fake。
    对接：tests 通过本函数受控注册；生产启动路径不得依赖 fake。
    二次开发：禁止注册会执行任意命令/读取任意路径的引擎。
    """
    name = str(getattr(engine, "name", "") or "").strip()
    if not name:
        raise ValueError("引擎名称不能为空")
    _ensure_defaults()
    if name in _REGISTRY and not overwrite:
        raise ValueError(f"引擎已注册：{name}")
    _REGISTRY[name] = engine


def unregister_engine(name: str) -> None:
    """
    用途：移除已注册引擎（测试清理）。
    对接：pytest fixture 的 teardown。
    二次开发：移除默认 lightweight 后下次 get 会自动恢复默认实例。
    """
    key = (name or "").strip()
    if not key:
        return
    _REGISTRY.pop(key, None)


def reset_registry() -> None:
    """
    用途：将注册表恢复为仅 lightweight（测试隔离）。
    对接：test_parse_engines / 注入 fake 的 fixture。
    """
    _REGISTRY.clear()
    _ensure_defaults()


def list_registered_engines() -> list[str]:
    """用途：返回当前已注册引擎名（排序），供测试与诊断。"""
    _ensure_defaults()
    return sorted(_REGISTRY.keys())


def resolve_engine_name(raw: object) -> str:
    """
    用途：从任务 payload 的 engine 字段解析引擎名。
    规则：
      - 缺失 / null / 空白字符串 → lightweight
      - 非空字符串 → 原样 strip 后作为名称
      - 其它类型（bool/数字/对象等）→ EngineUnavailableError（非法，不静默回退）
    """
    if raw is None:
        return DEFAULT_ENGINE_NAME
    # bool 是 int 子类，必须先排除
    if isinstance(raw, bool):
        raise EngineUnavailableError("解析引擎不可用：engine 必须为非空字符串名称")
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return DEFAULT_ENGINE_NAME
        return name
    raise EngineUnavailableError("解析引擎不可用：engine 必须为非空字符串名称")


def get_engine(name: str) -> ParseEngine:
    """
    用途：按名称取得已注册引擎；未注册则抛 EngineUnavailableError。
    对接：_run_parse / parse_with_engine。
    """
    _ensure_defaults()
    key = (name or "").strip() or DEFAULT_ENGINE_NAME
    engine = _REGISTRY.get(key)
    if engine is None:
        raise EngineUnavailableError(f"解析引擎不可用：未注册「{key}」")
    return engine


def parse_with_engine(engine_name: str, path: Path, original_name: str) -> tuple[str, str]:
    """
    用途：调度指定引擎解析文件。
    返回：(markdown 全文, 实际引擎 name)。
    对接：task_service._run_parse。
    二次开发：返回值必须是 str；非 str（含 None）在回传 task_service 前抛错，
    避免 _run_parse 把非法值写入 editor-state.parsedMarkdown。
    """
    engine = get_engine(engine_name)
    md = engine.parse(path, original_name)
    if not isinstance(md, str):
        # 协议约定 parse → str；非法返回值视为引擎不可用，禁止继续写 state
        raise EngineUnavailableError(
            f"解析引擎不可用：引擎「{engine_name}」返回值必须为字符串 Markdown，"
            f"实际为 {type(md).__name__}"
        )
    used = str(getattr(engine, "name", engine_name) or engine_name)
    return md, used


# 模块导入时安装默认引擎
_ensure_defaults()
