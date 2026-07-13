"""
模块：可插拔解析引擎调度测试
用途：验收 lightweight 默认路径、测试 fake 注入、非法 engine 失败且不覆盖 parsedMarkdown。
对接：parse_engines；task_service parse 任务；editor-state.parsedMarkdown。
二次开发：禁止依赖真实 MinerU/Docling/外网；fake 仅经 register_engine 注入。
"""

from io import BytesIO
from pathlib import Path

import pytest

from app.services import parse_engines


class _FakeParseEngine:
    """用途：测试专用引擎；返回固定 Markdown，不触碰真实解析器。"""

    name = "fake"

    def __init__(self, body: str = "# Fake\n\nfixture markdown from fake engine.\n"):
        self.body = body
        self.calls: list[tuple[str, str]] = []

    def parse(self, path: Path, original_name: str) -> str:
        self.calls.append((str(path), original_name))
        return self.body


class _NonStringParseEngine:
    """
    用途：测试专用；故意返回非字符串，验证不得覆盖旧 parsedMarkdown。
    二次开发：仅测试注册，不得进入默认生产注册表。
    """

    name = "bad_return"

    def __init__(self, body: object = None):
        self.body = body
        self.calls: list[tuple[str, str]] = []

    def parse(self, path: Path, original_name: str) -> object:
        self.calls.append((str(path), original_name))
        return self.body


@pytest.fixture(autouse=True)
def _reset_parse_engines():
    """用途：每测恢复仅 lightweight 的生产注册表。"""
    parse_engines.reset_registry()
    yield
    parse_engines.reset_registry()


def test_default_registry_only_lightweight():
    names = parse_engines.list_registered_engines()
    assert names == ["lightweight"]
    assert "fake" not in names


def test_resolve_engine_name_rules():
    assert parse_engines.resolve_engine_name(None) == "lightweight"
    assert parse_engines.resolve_engine_name("") == "lightweight"
    assert parse_engines.resolve_engine_name("   ") == "lightweight"
    assert parse_engines.resolve_engine_name("lightweight") == "lightweight"
    assert parse_engines.resolve_engine_name("  fake  ") == "fake"

    with pytest.raises(parse_engines.EngineUnavailableError) as ei:
        parse_engines.resolve_engine_name(True)
    assert "解析引擎不可用" in str(ei.value)

    with pytest.raises(parse_engines.EngineUnavailableError):
        parse_engines.resolve_engine_name(1)

    with pytest.raises(parse_engines.EngineUnavailableError):
        parse_engines.resolve_engine_name({"name": "x"})


def test_get_engine_unknown_raises():
    with pytest.raises(parse_engines.EngineUnavailableError) as ei:
        parse_engines.get_engine("mineru")
    assert "解析引擎不可用" in str(ei.value)
    assert "mineru" in str(ei.value)


def test_lightweight_parse_task_result_engine(client):
    """用途：旧路径零回归，result.engine=lightweight。"""
    proj = client.post("/api/projects", json={"name": "引擎轻量"}).json()
    pid = proj["id"]
    content = "# 招标文件\n\n项目概况：可插拔解析验收。\n".encode("utf-8")
    up = client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("tender.md", BytesIO(content), "text/markdown")},
    )
    assert up.status_code == 201

    parse_task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse"},
    )
    assert parse_task.status_code == 201
    body = parse_task.json()
    assert body["status"] == "success"
    assert body["result"]["engine"] == "lightweight"
    assert "可插拔解析验收" in (body["result"].get("parsedMarkdown") or "")

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert "可插拔解析验收" in (state.get("parsedMarkdown") or "")


def test_payload_blank_engine_defaults_lightweight(client):
    proj = client.post("/api/projects", json={"name": "空白引擎"}).json()
    pid = proj["id"]
    content = b"# blank engine\n\nok"
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("a.md", BytesIO(content), "text/markdown")},
    )
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "  "}},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "success"
    assert body["result"]["engine"] == "lightweight"


def test_fake_engine_injection_writes_fixture_markdown(client):
    """用途：测试注入 fake；成功写 fixture；下游仍读 parsedMarkdown。"""
    fake = _FakeParseEngine("# Fake\n\nfixture markdown from fake engine.\n")
    parse_engines.register_engine(fake)

    proj = client.post("/api/projects", json={"name": "假引擎"}).json()
    pid = proj["id"]
    content = b"# real file content should be ignored by fake\n"
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("scan.pdf", BytesIO(content), "application/pdf")},
    )

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "fake"}},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "success"
    assert body["result"]["engine"] == "fake"
    assert "fixture markdown from fake engine" in (body["result"].get("parsedMarkdown") or "")
    assert len(fake.calls) == 1

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert "fixture markdown from fake engine" in (state.get("parsedMarkdown") or "")
    # 下游契约：analyze 读取的是 editor-state.parsedMarkdown（此处不调真实 LLM）
    assert (state.get("parsedMarkdown") or "").startswith("# Fake")


def test_illegal_engine_fails_and_preserves_parsed_markdown(client):
    """用途：非法 engine 任务 failed，错误明确，不覆盖已有 parsedMarkdown，不调用 fake。"""
    fake = _FakeParseEngine()
    parse_engines.register_engine(fake)

    proj = client.post("/api/projects", json={"name": "非法引擎"}).json()
    pid = proj["id"]
    content = "# 原有解析\n\n请保留本正文。\n".encode("utf-8")
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("keep.md", BytesIO(content), "text/markdown")},
    )
    ok = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse"},
    )
    assert ok.json()["status"] == "success"
    before = client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
    assert "请保留本正文" in before

    # 未注册名称
    bad = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "docling"}},
    )
    assert bad.status_code == 201
    body = bad.json()
    assert body["status"] == "failed"
    err = body.get("error") or body.get("message") or ""
    assert "解析引擎不可用" in err
    assert fake.calls == []

    after = client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
    assert after == before

    # 非字符串类型
    bad_type = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": 1}},
    )
    body2 = bad_type.json()
    assert body2["status"] == "failed"
    assert "解析引擎不可用" in (body2.get("error") or body2.get("message") or "")
    assert (
        client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
        == before
    )
    assert fake.calls == []


def test_parse_with_engine_rejects_non_string_return():
    """用途：单元层：parse 返回 None/非 str 时抛 EngineUnavailableError。"""
    bad = _NonStringParseEngine(None)
    parse_engines.register_engine(bad)
    with pytest.raises(parse_engines.EngineUnavailableError) as ei:
        parse_engines.parse_with_engine(
            "bad_return", Path("dummy.md"), "dummy.md"
        )
    assert "解析引擎不可用" in str(ei.value)
    assert len(bad.calls) == 1

    bad2 = _NonStringParseEngine({"markdown": "x"})
    parse_engines.register_engine(bad2, overwrite=True)
    with pytest.raises(parse_engines.EngineUnavailableError) as ei2:
        parse_engines.parse_with_engine(
            "bad_return", Path("dummy.md"), "dummy.md"
        )
    assert "解析引擎不可用" in str(ei2.value)


def test_non_string_engine_return_fails_and_preserves_parsed_markdown(client):
    """
    用途：引擎返回非字符串时任务 failed，旧全文严格不变。
    对接：包8契约——任何引擎失败不得覆盖 editor-state.parsedMarkdown。
    """
    # 先用默认 lightweight 写入可识别全文
    proj = client.post("/api/projects", json={"name": "非字符串返回"}).json()
    pid = proj["id"]
    content = "# 旧全文锚点\n\n请严格保留本段正文，勿被坏引擎覆盖。\n".encode("utf-8")
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("keep.md", BytesIO(content), "text/markdown")},
    )
    ok = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse"},
    )
    assert ok.json()["status"] == "success"
    before = client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
    assert "请严格保留本段正文" in before
    assert "旧全文锚点" in before

    # 注入返回 None 的测试引擎（非默认注册）
    bad = _NonStringParseEngine(None)
    parse_engines.register_engine(bad)

    failed = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "bad_return"}},
    )
    assert failed.status_code == 201
    body = failed.json()
    assert body["status"] == "failed"
    err = body.get("error") or body.get("message") or ""
    assert "解析引擎不可用" in err
    assert len(bad.calls) == 1

    after = client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
    assert after == before

    # 再验证返回 dict 同样失败且全文不变
    bad_dict = _NonStringParseEngine({"text": "should not write"})
    parse_engines.register_engine(bad_dict, overwrite=True)
    failed2 = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "bad_return"}},
    )
    body2 = failed2.json()
    assert body2["status"] == "failed"
    assert "解析引擎不可用" in (body2.get("error") or body2.get("message") or "")
    assert (
        client.get(f"/api/projects/{pid}/editor-state").json()["parsedMarkdown"]
        == before
    )
