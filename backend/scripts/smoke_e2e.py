"""
模块：联调冒烟脚本
用途：不启动外网 LLM 的情况下验证 health / settings / projects / editor-state。
对接：在 backend 目录执行：python scripts/smoke_e2e.py
      需先 uvicorn 监听 127.0.0.1:8000
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as res:
            raw = res.read().decode("utf-8")
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def main() -> int:
    print("== health ==")
    code, health = req("GET", "/health")
    print(code, health)
    if code != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        print("FAIL health")
        return 1

    print("== settings put/get ==")
    code, _ = req(
        "PUT",
        "/settings",
        {
            "provider": "openai-compatible",
            "apiBaseUrl": "https://api.example.com/v1",
            "apiKey": "sk-smoke-plain",
            "model": "smoke-model",
            "parseStrategy": "light",
        },
    )
    print("put", code)
    code, settings = req("GET", "/settings")
    print("get", code, settings)
    if code != 200 or not isinstance(settings, dict):
        print("FAIL settings")
        return 1
    if settings.get("apiKey") != "sk-smoke-plain":
        print("FAIL settings key roundtrip")
        return 1

    print("== project create/list ==")
    code, proj = req("POST", "/projects", {"name": "冒烟联调项目", "industry": "测试"})
    print("create", code, proj)
    if code != 201 or not isinstance(proj, dict):
        print("FAIL create")
        return 1
    pid = proj["id"]
    code, listed = req("GET", "/projects")
    print("list", code, f"count={len(listed) if isinstance(listed, list) else '?'}")
    if code != 200:
        print("FAIL list")
        return 1

    print("== editor-state ==")
    code, st = req(
        "PUT",
        f"/projects/{pid}/editor-state",
        {
            "outline": [{"id": "n1", "title": "第一章", "children": []}],
            "analysisOverview": "冒烟概述",
            "mode": "ALIGNED",
            "guidance": {"targetWordCount": 80000},
        },
    )
    print("put editor", code)
    code, st = req("GET", f"/projects/{pid}/editor-state")
    print("get editor", code, st)
    if code != 200 or not isinstance(st, dict):
        print("FAIL editor-state")
        return 1
    if st.get("analysisOverview") != "冒烟概述":
        print("FAIL editor overview")
        return 1

    print("OK smoke e2e")
    return 0


if __name__ == "__main__":
    sys.exit(main())
