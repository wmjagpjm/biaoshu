"""
模块：响应矩阵 editor-state 测试
用途：验收评分点/技术要求到章节映射的读写、容错和旧库补列。
对接：GET|PUT /api/projects/{id}/editor-state；project_editor_states.response_matrix_json。
二次开发：新增矩阵字段或智能建议时必须补部分更新隔离与只读建议测试，避免防抖 PUT 清空已有映射。
"""

import json
from io import BytesIO

from sqlalchemy import create_engine, text

from app.core.database import SessionLocal, ensure_schema_columns
from app.models.entities import ProjectEditorStateRow
from app.services.llm_service import ChatResult


def _create_project(client) -> str:
    """用途：创建技术标项目并返回项目 id。"""
    return client.post("/api/projects", json={"name": "响应矩阵项目"}).json()["id"]


def test_response_matrix_empty_and_roundtrip(client):
    pid = _create_project(client)

    empty = client.get(f"/api/projects/{pid}/editor-state")
    assert empty.status_code == 200
    assert empty.json()["responseMatrix"] == []

    matrix = [
        {
            "id": "mx_req_1",
            "kind": "requirement",
            "sourceKey": "requirement:等保三级",
            "sourceIndex": 0,
            "sourceText": "等保三级",
            "weight": "",
            "chapterIds": ["chap_1"],
            "outlineNodeIds": ["node_1"],
            "status": "partial",
            "notes": "第四章已响应，实施章节待补。",
        }
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_1", "title": "响应章节"}],
            "chapters": [{"id": "chap_1", "title": "响应章节"}],
            "responseMatrix": matrix,
        },
    )
    assert put.status_code == 200
    assert put.json()["responseMatrix"] == matrix

    got = client.get(f"/api/projects/{pid}/editor-state").json()
    assert got["responseMatrix"] == matrix


def test_response_matrix_partial_updates_do_not_clear_other_fields(client):
    pid = _create_project(client)
    matrix = [
        {
            "id": "mx_score_1",
            "kind": "scoring",
            "sourceKey": "scoring:总体架构",
            "sourceIndex": 0,
            "sourceText": "总体架构",
            "weight": "20%",
            "chapterIds": ["chap_arch"],
            "outlineNodeIds": ["node_arch"],
            "status": "covered",
            "notes": "已覆盖。",
        }
    ]

    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_arch", "title": "总体架构", "children": []}],
            "chapters": [{"id": "chap_arch", "title": "总体架构"}],
            "responseMatrix": matrix,
        },
    )

    facts = [{"id": "f1", "category": "要求", "content": "等保三级", "source": "manual"}]
    put_facts = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": facts},
    )
    assert put_facts.status_code == 200
    body = put_facts.json()
    assert body["responseMatrix"] == matrix
    assert body["outline"][0]["id"] == "node_arch"
    assert body["facts"] == facts

    put_null = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"responseMatrix": None},
    )
    assert put_null.status_code == 200
    assert put_null.json()["responseMatrix"] == matrix

    put_matrix = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"responseMatrix": []},
    )
    assert put_matrix.status_code == 200
    assert put_matrix.json()["responseMatrix"] == []
    assert put_matrix.json()["outline"][0]["id"] == "node_arch"


def test_response_matrix_normalizes_invalid_rows(client):
    pid = _create_project(client)
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [{"id": "chap_1", "title": "总体架构"}],
            "responseMatrix": [
                {"kind": "unknown", "sourceText": "非法行"},
                {
                    "kind": "scoring",
                    "sourceIndex": "2",
                    "sourceText": " 总体架构 ",
                    "status": "bad",
                    "chapterIds": ["chap_1", "chap_1", ""],
                    "outlineNodeIds": "not-list",
                },
            ]
        },
    )
    assert put.status_code == 200
    rows = put.json()["responseMatrix"]
    assert len(rows) == 1
    assert rows[0]["id"].startswith("mx_")
    assert rows[0]["kind"] == "scoring"
    assert rows[0]["sourceKey"] == "scoring:总体架构"
    assert rows[0]["sourceIndex"] == 2
    assert rows[0]["sourceText"] == "总体架构"
    assert rows[0]["chapterIds"] == ["chap_1"]
    assert rows[0]["outlineNodeIds"] == []
    assert rows[0]["status"] == "uncovered"


def test_response_matrix_reconciles_dead_outline_and_chapter_ids(client):
    pid = _create_project(client)
    matrix = [
        {
            "id": "mx_req_1",
            "kind": "requirement",
            "sourceKey": "requirement:等保三级",
            "sourceIndex": 0,
            "sourceText": "等保三级",
            "chapterIds": ["chap_a"],
            "outlineNodeIds": ["node_a"],
            "status": "covered",
            "notes": "",
        }
    ]
    saved = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_a", "title": "安全方案"}],
            "chapters": [{"id": "chap_a", "title": "安全方案"}],
            "responseMatrix": matrix,
        },
    ).json()
    assert saved["responseMatrix"][0]["status"] == "covered"

    changed = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_b", "title": "新安全方案"}],
            "chapters": [{"id": "chap_b", "title": "新安全方案"}],
        },
    ).json()
    row = changed["responseMatrix"][0]
    assert row["chapterIds"] == []
    assert row["outlineNodeIds"] == []
    assert row["status"] == "uncovered"


def test_response_matrix_bad_json_does_not_break_get(client):
    pid = _create_project(client)
    db = SessionLocal()
    try:
        db.add(
            ProjectEditorStateRow(
                project_id=pid,
                mode="ALIGNED",
                response_matrix_json="{bad json",
            )
        )
        db.commit()
    finally:
        db.close()

    got = client.get(f"/api/projects/{pid}/editor-state")
    assert got.status_code == 200
    assert got.json()["responseMatrix"] == []


def test_response_matrix_column_is_added_to_old_sqlite_table(tmp_path):
    old_engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE project_editor_states (
              project_id VARCHAR(64) PRIMARY KEY,
              mode VARCHAR(32) NOT NULL DEFAULT 'ALIGNED',
              updated_at DATETIME NOT NULL
            )
            """
        )

    ensure_schema_columns(old_engine)

    with old_engine.connect() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(project_editor_states)"))
        }
    assert "response_matrix_json" in columns


def test_technical_export_includes_reconciled_response_matrix(client):
    """用途：验收技术标 Word 导出显示矩阵且不把失效关联当作覆盖。"""
    pid = _create_project(client)
    saved = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_security", "title": "安全设计"}],
            "chapters": [{"id": "chapter_security", "title": "安全实施"}],
            "responseMatrix": [
                {
                    "kind": "requirement",
                    "sourceText": "满足等保三级要求",
                    "chapterIds": ["chapter_security"],
                    "outlineNodeIds": ["node_security"],
                    "status": "covered",
                    "notes": "已在方案中说明。",
                }
            ],
        },
    )
    assert saved.status_code == 200

    # 模拟旧客户端或历史数据残留：导出路径必须独立收敛，而不是只信任 API 写入时的结果。
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, pid)
        assert row is not None
        row.response_matrix_json = json.dumps(
            [
                *saved.json()["responseMatrix"],
                {
                    "kind": "scoring",
                    "sourceText": "不存在的关联不得计为覆盖",
                    "weight": "10 分",
                    "chapterIds": ["chapter_missing"],
                    "outlineNodeIds": ["node_missing"],
                    "status": "covered",
                    "notes": "历史残留。",
                },
                {
                    "kind": "requirement",
                    "sourceText": "不响应项仍需留痕",
                    "status": "waived",
                    "notes": "不适用。",
                },
            ],
            ensure_ascii=False,
        )
        db.commit()
    finally:
        db.close()

    exported = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exported.status_code == 201
    stored_name = exported.json()["result"]["storedName"]
    downloaded = client.get(f"/api/projects/{pid}/export/download/{stored_name}")
    assert downloaded.status_code == 200

    from docx import Document  # type: ignore

    doc = Document(BytesIO(downloaded.content))
    assert any(paragraph.text == "六、响应矩阵" for paragraph in doc.paragraphs)
    matrix = doc.tables[-1]
    assert [cell.text for cell in matrix.rows[0].cells] == [
        "类型",
        "要求/评分点",
        "权重",
        "响应状态",
        "关联位置",
        "备注",
    ]
    table_text = "\n".join(cell.text for row in matrix.rows for cell in row.cells)
    assert "满足等保三级要求" in table_text
    assert "技术要求" in table_text
    assert "已覆盖" in table_text
    assert "正文：安全实施" in table_text
    assert "大纲：安全设计" in table_text
    assert "不存在的关联不得计为覆盖" in table_text
    assert "未覆盖" in table_text
    assert "chapter_missing" not in table_text
    assert "node_missing" not in table_text
    assert "不响应项仍需留痕" in table_text
    assert "不响应" in table_text


def test_business_export_does_not_include_response_matrix(client):
    """用途：验收响应矩阵仅属于技术标导出分支。"""
    project = client.post(
        "/api/projects", json={"name": "商务标响应矩阵隔离", "kind": "business"}
    ).json()
    pid = project["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": [
                {
                    "kind": "requirement",
                    "sourceText": "不应导出到商务标",
                    "status": "uncovered",
                }
            ]
        },
    )

    exported = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export", "payload": {"mode": "business"}},
    )
    assert exported.status_code == 201
    stored_name = exported.json()["result"]["storedName"]
    downloaded = client.get(f"/api/projects/{pid}/export/download/{stored_name}")
    assert downloaded.status_code == 200

    from docx import Document  # type: ignore

    doc = Document(BytesIO(downloaded.content))
    body = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert not any(paragraph.text == "六、响应矩阵" for paragraph in doc.paragraphs)
    assert "不应导出到商务标" not in body


def test_response_match_returns_sanitized_suggestions_without_writing_state(
    client, monkeypatch
):
    """用途：验收智能建议只写任务结果，且过滤重复来源、非法 ID 与 waived 行。"""
    pid = _create_project(client)
    saved = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [
                {"id": "node_arch", "title": "总体架构"},
                {"id": "node_security", "title": "安全设计"},
            ],
            "chapters": [
                {"id": "chapter_arch", "title": "架构正文"},
                {"id": "chapter_security", "title": "安全正文"},
            ],
            "responseMatrix": [
                {
                    "kind": "requirement",
                    "sourceKey": "requirement:等保三级",
                    "sourceText": "等保三级",
                    "status": "uncovered",
                },
                {
                    "kind": "scoring",
                    "sourceKey": "scoring:总体架构",
                    "sourceText": "总体架构",
                    "weight": "20%",
                    "status": "partial",
                },
                {
                    "kind": "requirement",
                    "sourceKey": "requirement:不适用",
                    "sourceText": "不适用要求",
                    "status": "waived",
                },
            ],
        },
    )
    assert saved.status_code == 200
    before = client.get(f"/api/projects/{pid}/editor-state").json()["responseMatrix"]

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "sourceKey": "requirement:等保三级",
                        "chapterIds": ["chapter_arch"],
                        "outlineNodeIds": ["node_arch"],
                        "status": "covered",
                        "confidence": 45,
                        "reason": "低置信度重复建议",
                    },
                    {
                        "sourceKey": "requirement:等保三级",
                        "chapterIds": ["chapter_security", "chapter_missing"],
                        "outlineNodeIds": ["node_security"],
                        "status": "covered",
                        "confidence": 91,
                        "reason": "安全章节直接回应等保要求",
                    },
                    {
                        "sourceKey": "scoring:总体架构",
                        "chapterIds": ["chapter_missing"],
                        "outlineNodeIds": ["node_missing"],
                        "status": "covered",
                        "confidence": 88,
                        "reason": "无效关联",
                    },
                    {
                        "sourceKey": "requirement:不适用",
                        "chapterIds": ["chapter_arch"],
                        "status": "covered",
                        "confidence": 99,
                    },
                    {
                        "sourceKey": "unknown:来源",
                        "chapterIds": ["chapter_arch"],
                        "status": "covered",
                        "confidence": 99,
                    },
                ],
                ensure_ascii=False,
            ),
            model="demo",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    response = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "response_match"},
    )
    assert response.status_code == 201
    task = response.json()
    assert task["status"] == "success"
    suggestions = task["result"]["suggestions"]
    assert len(suggestions) == 2
    requirement = suggestions[0]
    assert requirement["sourceKey"] == "requirement:等保三级"
    assert requirement["chapterIds"] == ["chapter_security"]
    assert requirement["outlineNodeIds"] == ["node_security"]
    assert requirement["status"] == "covered"
    assert requirement["confidence"] == 91
    assert requirement["base"] == {
        "chapterIds": [],
        "outlineNodeIds": [],
        "status": "uncovered",
    }
    invalid_links = suggestions[1]
    assert invalid_links["sourceKey"] == "scoring:总体架构"
    assert invalid_links["chapterIds"] == []
    assert invalid_links["outlineNodeIds"] == []
    assert invalid_links["status"] == "uncovered"
    assert task["result"]["skippedInvalidCount"] == 5
    after = client.get(f"/api/projects/{pid}/editor-state").json()["responseMatrix"]
    assert after == before


def test_response_matrix_version_stable_and_empty(client):
    """用途：空矩阵也有稳定版本；改概述不改矩阵版本。"""
    pid = _create_project(client)
    first = client.get(f"/api/projects/{pid}/editor-state").json()
    assert first["responseMatrix"] == []
    assert isinstance(first["responseMatrixVersion"], str)
    assert first["responseMatrixVersion"].startswith("rmv_")
    empty_version = first["responseMatrixVersion"]

    second = client.get(f"/api/projects/{pid}/editor-state").json()
    assert second["responseMatrixVersion"] == empty_version

    matrix = [
        {
            "id": "mx_req_v1",
            "kind": "requirement",
            "sourceKey": "requirement:等保三级",
            "sourceIndex": 0,
            "sourceText": "等保三级",
            "chapterIds": ["chap_1"],
            "outlineNodeIds": ["node_1"],
            "status": "partial",
            "notes": "初稿",
        }
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_1", "title": "安全"}],
            "chapters": [{"id": "chap_1", "title": "安全"}],
            "responseMatrix": matrix,
            "responseMatrixVersion": empty_version,
        },
    )
    assert put.status_code == 200
    matrix_version = put.json()["responseMatrixVersion"]
    assert matrix_version != empty_version
    assert put.json()["responseMatrix"][0]["sourceText"] == "等保三级"

    overview_only = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"analysisOverview": "仅改概述不应改变矩阵版本"},
    )
    assert overview_only.status_code == 200
    assert overview_only.json()["responseMatrixVersion"] == matrix_version
    assert overview_only.json()["analysisOverview"] == "仅改概述不应改变矩阵版本"


def test_response_matrix_version_match_save_and_stale_conflict(client):
    """用途：版本匹配可保存；两客户端陈旧版本 409 且远端矩阵不变。"""
    pid = _create_project(client)
    base = client.get(f"/api/projects/{pid}/editor-state").json()
    v0 = base["responseMatrixVersion"]

    client_a_matrix = [
        {
            "id": "mx_a",
            "kind": "requirement",
            "sourceKey": "requirement:A端写入",
            "sourceIndex": 0,
            "sourceText": "A端写入",
            "chapterIds": [],
            "outlineNodeIds": [],
            "status": "uncovered",
            "notes": "A",
        }
    ]
    a_ok = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": client_a_matrix,
            "responseMatrixVersion": v0,
        },
    )
    assert a_ok.status_code == 200
    assert a_ok.json()["responseMatrix"][0]["sourceText"] == "A端写入"
    v_a = a_ok.json()["responseMatrixVersion"]

    client_b_stale = [
        {
            "id": "mx_b",
            "kind": "requirement",
            "sourceKey": "requirement:B端陈旧",
            "sourceIndex": 0,
            "sourceText": "B端陈旧",
            "chapterIds": [],
            "outlineNodeIds": [],
            "status": "uncovered",
            "notes": "B",
        }
    ]
    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": client_b_stale,
            "responseMatrixVersion": v0,
            "analysisOverview": "冲突时不得写入概述",
        },
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert "message" in detail and detail["message"]
    assert detail["currentResponseMatrixVersion"] == v_a
    assert detail["responseMatrix"][0]["sourceText"] == "A端写入"

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["responseMatrix"][0]["sourceText"] == "A端写入"
    assert after["responseMatrixVersion"] == v_a
    assert after.get("analysisOverview") in (None, "", after.get("analysis", {}).get("overview"))
    # 冲突整包不写：概述不应被陈旧请求改掉
    assert (after.get("analysisOverview") or "") != "冲突时不得写入概述"

    matched = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": client_b_stale,
            "responseMatrixVersion": v_a,
        },
    )
    assert matched.status_code == 200
    assert matched.json()["responseMatrix"][0]["sourceText"] == "B端陈旧"


def test_response_matrix_legacy_put_without_version(client):
    """用途：旧客户端不带 responseMatrixVersion 仍可写矩阵。"""
    pid = _create_project(client)
    matrix = [
        {
            "kind": "scoring",
            "sourceText": "旧客户端写入",
            "weight": "10%",
            "chapterIds": [],
            "outlineNodeIds": [],
            "status": "uncovered",
        }
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"responseMatrix": matrix},
    )
    assert put.status_code == 200
    assert put.json()["responseMatrix"][0]["sourceText"] == "旧客户端写入"
    assert put.json()["responseMatrixVersion"].startswith("rmv_")


def test_response_matrix_concurrent_versioned_puts_one_wins(client):
    """
    用途：独立 Session 并发带同一 expected version 的矩阵 PUT，恰好一方成功一方 409。
    对接：editor_state_service 写锁；不得用顺序两次调用代替。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.core.database import SessionLocal
    from app.services import editor_state_service
    from app.services.editor_state_service import ResponseMatrixVersionConflict

    pid = _create_project(client)
    base = client.get(f"/api/projects/{pid}/editor-state").json()
    v0 = base["responseMatrixVersion"]
    workspace_id = "ws_local"

    def _matrix(label: str) -> list[dict]:
        return [
            {
                "id": f"mx_{label}",
                "kind": "requirement",
                "sourceKey": f"requirement:{label}",
                "sourceIndex": 0,
                "sourceText": label,
                "chapterIds": [],
                "outlineNodeIds": [],
                "status": "uncovered",
                "notes": label,
            }
        ]

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str | None]] = []

    def worker(label: str) -> tuple[str, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                data = editor_state_service.upsert_editor_state(
                    db,
                    workspace_id,
                    pid,
                    response_matrix=_matrix(label),
                    response_matrix_version=v0,
                )
                return ("ok", data["responseMatrix"][0]["sourceText"])
            except ResponseMatrixVersionConflict as exc:
                # 冲突方必须看到获胜者矩阵
                winner = (
                    exc.current_matrix[0]["sourceText"]
                    if exc.current_matrix
                    else None
                )
                return ("conflict", winner)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "并发甲"), pool.submit(worker, "并发乙")]
        outcomes = [f.result(timeout=15) for f in futures]

    statuses = sorted(o[0] for o in outcomes)
    assert statuses == ["conflict", "ok"], outcomes
    ok_label = next(o[1] for o in outcomes if o[0] == "ok")
    conflict_seen = next(o[1] for o in outcomes if o[0] == "conflict")
    assert ok_label in ("并发甲", "并发乙")
    assert conflict_seen == ok_label

    final = client.get(f"/api/projects/{pid}/editor-state").json()
    assert final["responseMatrix"][0]["sourceText"] == ok_label
    assert final["responseMatrixVersion"] != v0
