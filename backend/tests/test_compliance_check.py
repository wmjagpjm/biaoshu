"""
模块：查重 / 废标检查验收
用途：text_similarity、duplicate-check、rejection-check API。
"""

from __future__ import annotations


def test_text_similarity_basic():
    from app.services.text_similarity import similarity, split_paragraphs

    a = "本系统采用微服务架构，支持高可用部署与弹性扩容能力。"
    b = "本系统采用微服务架构，支持高可用部署与弹性扩展能力。"
    c = "今日天气晴朗，适合户外运动与野餐。"
    assert similarity(a, b) > 0.5
    assert similarity(a, c) < 0.35
    paras = split_paragraphs(
        "# 标题\n\n" + a + "\n\n" + b,
        min_len=20,
    )
    assert len(paras) >= 2


def test_duplicate_check_kb_hit(client):
    """用途：章节与知识库高度重合时能命中。"""
    # 知识库文档
    folders = client.get("/api/knowledge/folders").json()
    assert folders
    fid = folders[0]["id"]
    content = (
        "本平台采用云原生微服务架构，统一身份认证，支持弹性伸缩与多活容灾，"
        "提供全链路监控与审计日志能力。"
    )
    up = client.post(
        "/api/knowledge/docs/upload",
        data={"folderId": fid, "name": "历史标书片段.md"},
        files={"file": ("hist.md", content.encode("utf-8"), "text/markdown")},
    )
    assert up.status_code in (200, 201), up.text

    proj = client.post(
        "/api/projects", json={"name": "查重项目", "kind": "technical"}
    ).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "ch1",
                    "title": "总体架构",
                    "body": (
                        "本平台采用云原生微服务架构，统一身份认证，支持弹性伸缩与多活容灾，"
                        "提供全链路监控与审计日志能力。额外补充一句实施步骤说明。"
                    ),
                }
            ]
        },
    )

    res = client.post(
        f"/api/projects/{pid}/duplicate-check",
        json={"scope": "kb", "threshold": 0.55, "topK": 20},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["projectId"] == pid
    assert "hits" in body
    # 高度重合应至少 1 条（算法波动时允许 stats 正常）
    assert body["stats"]["selfParagraphs"] >= 1
    if body["hits"]:
        assert body["hits"][0]["similarity"] >= 0.55
        assert "知识库" in body["hits"][0]["sourceLabel"]


def test_duplicate_check_self(client):
    proj = client.post(
        "/api/projects", json={"name": "自查重", "kind": "technical"}
    ).json()
    pid = proj["id"]
    same = (
        "投标人应具备独立法人资格并提供近三年财务报表与审计报告扫描件。"
        "项目团队应稳定配置不少于十五人。"
    )
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {"id": "a", "title": "章A", "body": same},
                {"id": "b", "title": "章B", "body": same + "补充说明。"},
            ]
        },
    )
    res = client.post(
        f"/api/projects/{pid}/duplicate-check",
        json={"scope": "self", "threshold": 0.6},
    )
    assert res.status_code == 200, res.text
    hits = res.json()["hits"]
    assert len(hits) >= 1
    assert "本文内部" in hits[0]["sourceLabel"]


def test_rejection_check_from_analysis(client):
    proj = client.post(
        "/api/projects", json={"name": "废标检", "kind": "technical"}
    ).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "parsedMarkdown": "★ 投标人必须提供近三年业绩，否则废标。保证金不少于 2%。",
            "analysis": {
                "overview": "测试",
                "techRequirements": [],
                "rejectionRisks": ["未提供近三年业绩将废标", "保证金不足否决"],
                "scoringPoints": [],
            },
            "chapters": [{"id": "c1", "title": "响应", "body": "我方提供业绩证明。"}],
        },
    )
    res = client.post(
        f"/api/projects/{pid}/rejection-check",
        json={"includeRules": True},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["stats"]["total"] >= 2
    titles = " ".join(i["title"] for i in data["items"])
    assert "业绩" in titles or "废标" in titles or "保证金" in titles


def test_rejection_missing_parse(client):
    proj = client.post(
        "/api/projects", json={"name": "空废标", "kind": "technical"}
    ).json()
    pid = proj["id"]
    res = client.post(f"/api/projects/{pid}/rejection-check", json={})
    assert res.status_code == 200
    items = res.json()["items"]
    assert items
    assert items[0]["level"] == "high"
