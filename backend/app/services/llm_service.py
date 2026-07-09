"""
模块：LLM 调用服务（OpenAI 兼容）
用途：使用工作空间内用户自备的 apiBaseUrl + apiKey + model 发起 chat/completions。
对接：
  - settings 表（明文 Key）
  - revise_service、POST /api/llm/test
二次开发：
  - 流式/SSE 可在此扩展 stream=True
  - 非 OpenAI 兼容供应商可按 provider 分支
  - 禁止把 Key 写进日志全文
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.services import settings_service


class LlmConfigError(Exception):
    """用途：未配置 Base/Key/模型等，前端应提示去设置页填写。"""


class LlmCallError(Exception):
    """用途：上游模型 HTTP/协议错误。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ChatResult:
    """用途：一次对话结果摘要。"""

    content: str
    model: str
    raw_usage: dict | None = None


def _chat_url(api_base_url: str) -> str:
    """用途：拼出 chat/completions 完整 URL（兼容已带/不带 /v1）。"""
    base = api_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat_completion(
    db: Session,
    workspace_id: str,
    *,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    timeout_sec: float = 120.0,
) -> ChatResult:
    """
    用途：同步调用 OpenAI 兼容 Chat Completions。
    参数：messages 形如 [{role, content}, ...]
    异常：LlmConfigError / LlmCallError
    """
    cfg = settings_service.get_or_create_settings(db, workspace_id)
    if not (cfg.api_base_url or "").strip():
        raise LlmConfigError("未配置 API Base URL，请到设置页填写")
    if not (cfg.api_key or "").strip():
        # Ollama 等本地可无 Key，但多数云需 Key；允许空 Key 仅当 base 含 localhost/127.0.0.1
        base_l = cfg.api_base_url.lower()
        if "127.0.0.1" not in base_l and "localhost" not in base_l:
            raise LlmConfigError("未配置 API Key，请到设置页填写")
    if not (cfg.model or "").strip():
        raise LlmConfigError("未配置模型名，请到设置页填写")

    url = _chat_url(cfg.api_base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
    }
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
    }

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            res = client.post(url, headers=headers, json=payload)
    except httpx.RequestError as exc:
        raise LlmCallError(f"连接模型服务失败: {exc}") from exc

    if res.status_code >= 400:
        # 不回传完整 Authorization；仅截断 body
        detail = (res.text or "")[:500]
        raise LlmCallError(
            f"模型服务返回 {res.status_code}: {detail}",
            status_code=res.status_code,
        )

    try:
        data = res.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmCallError(f"模型响应格式异常: {(res.text or '')[:300]}") from exc

    usage = data.get("usage") if isinstance(data, dict) else None
    return ChatResult(content=str(content or "").strip(), model=cfg.model, raw_usage=usage)


def test_connection(db: Session, workspace_id: str) -> dict:
    """
    用途：用极短 prompt 验证 Key/Base/模型是否可用。
    对接：POST /api/llm/test
    """
    result = chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "user",
                "content": "请只回复两个字：成功",
            }
        ],
        temperature=0,
        timeout_sec=60.0,
    )
    return {
        "ok": True,
        "model": result.model,
        "reply": result.content[:200],
    }
