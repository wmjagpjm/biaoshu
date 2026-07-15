"""
模块：P12B-A editor-state 全状态版本与可选 CAS 定向测试
用途：验收共享 13 键规范哈希、GET/PUT stateVersion、可选 expectedStateVersion CAS、
  全状态优先于矩阵版本、真并发单胜与脱敏 409。
对接：GET|PUT /api/projects/{id}/editor-state；editor_state_service；
  editor_state_checkpoint_service；P12A 检查点。
二次开发：仅本地 SQLite 与固定合成数据；禁止 or True、宽泛状态码、顺序调用冒充并发。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import SessionLocal, engine
from app.models.entities import ProjectEditorStateRow
from app.services import editor_state_service
from app.services.editor_state_service import ResponseMatrixVersionConflict

_SNAPSHOT_KEYS = (
    "outline",
    "chapters",
    "facts",
    "mode",
    "analysis",
    "responseMatrix",
    "guidance",
    "parsedMarkdown",
    "businessQualify",
    "businessToc",
    "businessQuote",
    "businessCommit",
    "analysisOverview",
)
_SNAPSHOT_KEY_SET = frozenset(_SNAPSHOT_KEYS)
_FORBIDDEN_VERSION_KEYS = frozenset(
    {
        "projectId",
        "updatedAt",
        "responseMatrixVersion",
        "workspaceId",
        "name",
        "userId",
        "csrf",
        "apiKey",
        "taskId",
        "batchId",
        "path",
        "token",
    }
)
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_SECRET = "SECRET_P12B_SHOULD_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state"
_WS = "ws_local"


def _create_project(
    client: TestClient,
    name: str = "P12B技术标",
    kind: str = "technical",
) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _canonical_snapshot_from_state(state: dict) -> dict:
    """用途：测试侧独立抽取 13 键，禁止信任服务端实现。"""
    return {key: state.get(key) for key in _SNAPSHOT_KEYS}


def _canonical_json(snapshot: dict) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _independent_state_version(state: dict) -> str:
    """用途：测试侧独立 SHA-256 前 32 hex，与实现交叉核对。"""
    snap = _canonical_snapshot_from_state(state)
    digest = hashlib.sha256(_canonical_json(snap).encode("utf-8")).hexdigest()
    return "esv_" + digest[:32]


def _assert_state_version_format(version: str) -> None:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version


def _seed_technical(client: TestClient, pid: str) -> dict:
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [
                {
                    "id": "node_root",
                    "title": "根节点",
                    "children": [
                        {"id": "node_a", "title": "子A", "children": []},
                    ],
                }
            ],
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "架构正文。",
                    "status": "pending",
                    "preview": "架构正文。",
                    "wordCount": 5,
                }
            ],
            "facts": [{"id": "fact_1", "text": "事实一"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": "分析概述",
                "techRequirements": ["要求甲"],
                "rejectionRisks": [],
                "scoringPoints": ["评分点1"],
            },
            "responseMatrix": [
                {
                    "id": "mx_1",
                    "kind": "requirement",
                    "sourceKey": "requirement:要求甲",
                    "sourceIndex": 0,
                    "sourceText": "要求甲",
                    "chapterIds": ["chap_a"],
                    "outlineNodeIds": ["node_a"],
                    "status": "partial",
                    "notes": "备注",
                }
            ],
            "guidance": {"hints": ["提示1"]},
            "parsedMarkdown": "# 招标文件\n正文",
            "analysisOverview": "分析概述",
        },
    )
    assert put.status_code == 200, put.text
    return put.json()


def _seed_business(client: TestClient, pid: str) -> dict:
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "mode": "ALIGNED",
            "businessQualify": [{"name": "资质A"}],
            "businessToc": [{"title": "目录一"}],
            "businessQuote": {"rows": [{"item": "报价项", "amount": 100}]},
            "businessCommit": [{"text": "承诺一"}],
            "analysisOverview": "商务概述",
        },
    )
    assert put.status_code == 200, put.text
    return put.json()


def _assert_no_leak(blob: str) -> None:
    assert _SECRET not in blob
    assert _PATH_MARKER not in blob
    assert "Traceback" not in blob
    assert "SELECT" not in blob
    assert "sqlite" not in blob.lower()


# ---------- 1. 独立规范化哈希与 GET stateVersion ----------


def test_empty_technical_business_state_version_matches_independent_hash(client):
    """用途：空态/技术标/商务标 stateVersion 与测试侧独立哈希精确相等。"""
    # 空态
    pid_empty = _create_project(client, name="P12B空态")
    empty = client.get(f"/api/projects/{pid_empty}/editor-state")
    assert empty.status_code == 200, empty.text
    empty_body = empty.json()
    assert "stateVersion" in empty_body
    _assert_state_version_format(empty_body["stateVersion"])
    assert empty_body["stateVersion"] == _independent_state_version(empty_body)
    # 证明 projectId/updatedAt/responseMatrixVersion 不改变版本：两项目空态同版本
    pid_empty2 = _create_project(client, name="P12B空态2")
    empty2 = client.get(f"/api/projects/{pid_empty2}/editor-state").json()
    assert empty2["stateVersion"] == empty_body["stateVersion"]
    assert empty2["projectId"] != empty_body["projectId"]

    # 技术标
    pid_tech = _create_project(client, name="P12B技术")
    tech = _seed_technical(client, pid_tech)
    assert "stateVersion" in tech
    _assert_state_version_format(tech["stateVersion"])
    assert tech["stateVersion"] == _independent_state_version(tech)
    got_tech = client.get(f"/api/projects/{pid_tech}/editor-state").json()
    assert got_tech["stateVersion"] == tech["stateVersion"]
    assert got_tech["stateVersion"] == _independent_state_version(got_tech)

    # 商务标
    pid_biz = _create_project(client, name="P12B商务", kind="business")
    biz = _seed_business(client, pid_biz)
    assert "stateVersion" in biz
    _assert_state_version_format(biz["stateVersion"])
    assert biz["stateVersion"] == _independent_state_version(biz)
    got_biz = client.get(f"/api/projects/{pid_biz}/editor-state").json()
    assert got_biz["stateVersion"] == biz["stateVersion"]


def test_forbidden_fields_excluded_from_state_version(client):
    """用途：projectId/updatedAt/responseMatrixVersion 与敏感标记不得进入全状态版本。"""
    pid = _create_project(client)
    state = _seed_technical(client, pid)
    snap = _canonical_snapshot_from_state(state)
    assert set(snap.keys()) == _SNAPSHOT_KEY_SET
    for bad in _FORBIDDEN_VERSION_KEYS:
        assert bad not in snap
    # 序列化字节不得含敏感标记（规范 13 键内容本身无这些键）
    raw = _canonical_json(snap)
    assert _SECRET not in raw
    assert "projectId" not in raw
    assert "updatedAt" not in raw
    assert "responseMatrixVersion" not in raw
    assert state["stateVersion"] == _independent_state_version(state)


# ---------- 2. P12A 检查点版本一致性 ----------


def test_p12a_checkpoint_version_equals_current_editor_state(client):
    """用途：P12A 创建检查点版本精确等于当前 GET stateVersion；snapshot 仍 13 键。"""
    pid = _create_project(client)
    _seed_technical(client, pid)
    current = client.get(f"/api/projects/{pid}/editor-state").json()
    current_version = current["stateVersion"]
    _assert_state_version_format(current_version)

    created = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert created.status_code == 201, created.text
    meta = created.json()
    assert meta["stateVersion"] == current_version

    detail = client.get(
        f"/api/projects/{pid}/editor-state-checkpoints/{meta['checkpointId']}"
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["stateVersion"] == current_version
    snap = body["snapshot"]
    assert set(snap.keys()) == _SNAPSHOT_KEY_SET
    for bad in _FORBIDDEN_VERSION_KEYS:
        assert bad not in snap
    # 独立哈希应与检查点版本一致
    assert _independent_state_version({**snap, "projectId": pid}) == current_version


# ---------- 3. CAS 成功路径与版本变化 ----------


def test_matching_expected_writes_and_returns_new_version(client):
    """用途：当前 expected 写成功并返回新版本；内容变化则版本变化。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]

    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "expectedStateVersion": v0,
            "facts": [{"id": "fact_new", "text": "新事实"}],
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["facts"][0]["id"] == "fact_new"
    v1 = body["stateVersion"]
    _assert_state_version_format(v1)
    assert v1 != v0
    assert v1 == _independent_state_version(body)

    again = client.get(f"/api/projects/{pid}/editor-state").json()
    assert again["stateVersion"] == v1
    assert again["facts"][0]["id"] == "fact_new"


def test_updated_at_alone_does_not_change_state_version(client):
    """用途：仅 updatedAt 变化时 stateVersion 不变（内容未变的兼容写）。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    updated_at_0 = base.get("updatedAt")

    # 不带 expected 的兼容写：写入相同 facts，会刷新 updatedAt
    same_facts = base["facts"]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": same_facts},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["stateVersion"] == v0
    # updatedAt 通常会变；若碰巧相同也不否定版本稳定
    if updated_at_0 is not None and body.get("updatedAt") is not None:
        # 至少证明版本与独立哈希仍一致
        assert body["stateVersion"] == _independent_state_version(body)


# ---------- 4. 过期 expected 固定 409 与零写 ----------


def test_stale_expected_returns_fixed_409_and_zero_write(client):
    """用途：过期 expected 固定最小 409、投稿零写、错误不泄漏。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]

    # 先用兼容写改内容，使 v0 过期
    mid = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "fact_mid", "text": "中间态"}]},
    )
    assert mid.status_code == 200, mid.text
    v_mid = mid.json()["stateVersion"]
    assert v_mid != v0

    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "expectedStateVersion": v0,
            "facts": [{"id": "fact_stale", "text": _SECRET}],
            "analysisOverview": "陈旧覆盖概述",
            "parsedMarkdown": f"leak:{_PATH_MARKER}",
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert isinstance(detail, dict)
    assert set(detail.keys()) == {
        "code",
        "message",
        "currentStateVersion",
    }
    assert detail["code"] == "editor_state_version_conflict"
    assert detail["message"] == "编辑内容已被其他操作更新，请重新载入后再保存"
    assert detail["currentStateVersion"] == v_mid
    _assert_no_leak(conflict.text)
    # 不得回显当前正文/矩阵
    assert "responseMatrix" not in detail
    assert "outline" not in detail
    assert "facts" not in detail
    # 精确：冲突响应正文不得出现项目 ID（版本串可出现 currentStateVersion）
    assert pid not in conflict.text

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == v_mid
    assert after["facts"][0]["id"] == "fact_mid"
    assert after["facts"][0]["text"] != _SECRET
    assert (after.get("analysisOverview") or "") != "陈旧覆盖概述"


def test_stale_expected_service_session_rollback(client):
    """用途：CAS 冲突后 Session 无打开写事务（service 层显式 rollback）。"""
    conflict_cls = getattr(editor_state_service, "EditorStateVersionConflict", None)
    assert conflict_cls is not None, "EditorStateVersionConflict 尚未实现"

    pid = _create_project(client)
    base = client.get(f"/api/projects/{pid}/editor-state").json()
    # 旧实现无 stateVersion 时此处即失败（failure-first）
    v0 = base["stateVersion"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "x", "text": "changed"}]},
    )

    db = SessionLocal()
    try:
        with pytest.raises(conflict_cls) as ei:
            editor_state_service.upsert_editor_state(
                db,
                _WS,
                pid,
                expected_state_version=v0,
                facts=[{"id": "stale", "text": "no"}],
            )
        assert ei.value.current_state_version
        assert not db.in_transaction()
    finally:
        db.close()


# ---------- 5. 格式 422 与缺失兼容 ----------


def test_invalid_expected_format_is_422_and_never_enters_service(client):
    """用途：expected 错误格式精确 422；不得进入 service 写路径。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    before = client.get(f"/api/projects/{pid}/editor-state").json()

    invalids = [
        "esv_ABCDEF0123456789abcdef0123456789",  # 大写
        "esv_abc",  # 过短
        "rmv_" + "a" * 32,  # 错误前缀
        "esv_" + "g" * 32,  # 非 hex
        " esv_" + "a" * 32,  # 空白
        "esv_" + "a" * 31 + "A",  # 混入大写
    ]
    for bad in invalids:
        res = client.put(
            f"/api/projects/{pid}/editor-state",
            json={
                "expectedStateVersion": bad,
                "facts": [{"id": "should_not", "text": "no"}],
            },
        )
        assert res.status_code == 422, (bad, res.text)

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == before["stateVersion"]
    assert after["facts"] == before["facts"]


def test_missing_expected_keeps_compatible_write_success(client):
    """
    用途：缺失 expected 保持兼容成功；明确这不是最终安全恢复门。
    二次开发：P12B-A 迁移窗口允许无版本覆盖，不得据此声称已阻止迟到写入。
    """
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]

    # 无 expected：可直接覆盖
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "compat", "text": "兼容覆盖"}]},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["facts"][0]["id"] == "compat"
    assert body["stateVersion"] != v0
    # 证明无 CAS 门：再次无 expected 仍成功
    put2 = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "compat2", "text": "再次覆盖"}]},
    )
    assert put2.status_code == 200, put2.text
    assert put2.json()["facts"][0]["id"] == "compat2"


# ---------- 6. 真并发：同一 expected 最多一胜 ----------


def test_concurrent_same_expected_at_most_one_success(client):
    """
    用途：两个独立 Session/线程 + barrier 同 expected 真并发；最多一个 200，另一 409。
    禁止顺序调用假装并发。
    """
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str | None]] = []

    conflict_cls = getattr(editor_state_service, "EditorStateVersionConflict", None)
    assert conflict_cls is not None, "EditorStateVersionConflict 尚未实现"

    def worker(label: str) -> tuple[str, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                data = editor_state_service.upsert_editor_state(
                    db,
                    _WS,
                    pid,
                    expected_state_version=v0,
                    facts=[{"id": f"fact_{label}", "text": label}],
                )
                return ("ok", data["facts"][0]["id"] if data.get("facts") else None)
            except conflict_cls as exc:  # type: ignore[misc]
                return ("conflict", exc.current_state_version)
            except ResponseMatrixVersionConflict:
                return ("matrix_conflict", None)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(worker, "并发甲"),
            pool.submit(worker, "并发乙"),
        ]
        outcomes = [f.result(timeout=20) for f in futures]

    statuses = sorted(o[0] for o in outcomes)
    assert statuses == ["conflict", "ok"], outcomes
    ok_id = next(o[1] for o in outcomes if o[0] == "ok")
    assert ok_id in ("fact_并发甲", "fact_并发乙")

    final = client.get(f"/api/projects/{pid}/editor-state").json()
    assert final["facts"][0]["id"] == ok_id
    assert final["stateVersion"] != v0
    conflict_version = next(o[1] for o in outcomes if o[0] == "conflict")
    assert conflict_version == final["stateVersion"]


# ---------- 7. expected + responseMatrixVersion 优先级 ----------


def test_full_state_conflict_precedes_matrix_conflict(client):
    """用途：全状态 expected 不匹配时返回全状态 409，不走矩阵冲突正文。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    matrix_v0 = base["responseMatrixVersion"]

    # 改非矩阵字段使全状态版本变化，矩阵版本可能仍变（因 reconcile）；再单独只改 facts
    mid = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "fact_changed", "text": "改事实"}]},
    )
    assert mid.status_code == 200
    v_mid = mid.json()["stateVersion"]
    assert v_mid != v0

    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "expectedStateVersion": v0,
            "responseMatrixVersion": matrix_v0,
            "responseMatrix": [
                {
                    "id": "mx_stale",
                    "kind": "requirement",
                    "sourceKey": "requirement:陈旧",
                    "sourceIndex": 0,
                    "sourceText": "陈旧矩阵",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "uncovered",
                    "notes": "",
                }
            ],
            "analysisOverview": "不得写入",
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail.get("code") == "editor_state_version_conflict"
    assert detail.get("currentStateVersion") == v_mid
    # 全状态冲突：不得出现矩阵三方合并字段
    assert "responseMatrix" not in detail
    assert "currentResponseMatrixVersion" not in detail
    _assert_no_leak(conflict.text)

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == v_mid
    assert after["facts"][0]["id"] == "fact_changed"
    assert (after.get("analysisOverview") or "") != "不得写入"


def test_matching_full_state_then_matrix_conflict_keeps_existing_detail(client):
    """用途：全状态匹配后矩阵冲突仍保持既有 409/三方合并契约。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    matrix_v0 = base["responseMatrixVersion"]

    # 另一端只更新矩阵（无全状态 expected），产生新矩阵版本；全状态版本也会变
    # 为测「全状态匹配 + 矩阵不匹配」，先取当前全状态版本与陈旧矩阵版本组合：
    # 步骤：A 写入矩阵 v1；B 持有旧 matrix_v0 但拿最新 full version —— 需在锁外准备。
    # 先让 A 更新矩阵：
    a = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": [
                {
                    "id": "mx_a",
                    "kind": "requirement",
                    "sourceKey": "requirement:A端",
                    "sourceIndex": 0,
                    "sourceText": "A端写入",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "covered",
                    "notes": "A",
                }
            ],
            "responseMatrixVersion": matrix_v0,
        },
    )
    assert a.status_code == 200, a.text
    after_a = a.json()
    v_full = after_a["stateVersion"]
    v_matrix_a = after_a["responseMatrixVersion"]
    assert v_matrix_a != matrix_v0

    # B：全状态用最新 v_full，矩阵版本仍用过期 matrix_v0
    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "expectedStateVersion": v_full,
            "responseMatrixVersion": matrix_v0,
            "responseMatrix": [
                {
                    "id": "mx_b",
                    "kind": "requirement",
                    "sourceKey": "requirement:B端",
                    "sourceIndex": 0,
                    "sourceText": "B端陈旧",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "uncovered",
                    "notes": "B",
                }
            ],
            "analysisOverview": "矩阵冲突不得写概述",
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    # 既有矩阵冲突：精确键集，明确不是全状态冲突
    assert set(detail.keys()) == {
        "message",
        "responseMatrix",
        "currentResponseMatrixVersion",
    }
    assert detail["message"] == "响应矩阵已被其他终端更新，请重新载入后再保存"
    assert detail["currentResponseMatrixVersion"] == v_matrix_a
    assert detail["responseMatrix"][0]["sourceText"] == "A端写入"
    assert "code" not in detail
    assert "currentStateVersion" not in detail

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["responseMatrix"][0]["sourceText"] == "A端写入"
    assert (after.get("analysisOverview") or "") != "矩阵冲突不得写概述"


# ---------- 8. 敏感标记不出现在冲突响应 ----------


def test_conflict_response_excludes_sensitive_markers(client):
    """用途：冲突响应不含正文、路径、SQL、异常或测试秘密。"""
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": f"body:{_SECRET}"},
    )
    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "expectedStateVersion": v0,
            "parsedMarkdown": f"attack:{_PATH_MARKER}:{_SECRET}",
        },
    )
    assert conflict.status_code == 409
    _assert_no_leak(conflict.text)
    detail = conflict.json()["detail"]
    assert set(detail.keys()) == {"code", "message", "currentStateVersion"}


# ---------- 9. 一次锁/一次读取、提交后零刷新、commit 回滚 ----------


def _classify_target_sql(statements: list[str]) -> dict[str, list[str]]:
    """
    用途：按目标表过滤 SQL，仅统计 projects / project_editor_states 的锁与读取语义。
    二次开发：忽略其它表；UPDATE/SELECT 判断基于规范化语句前缀。
    """
    project_updates: list[str] = []
    project_selects: list[str] = []
    editor_selects: list[str] = []
    editor_updates: list[str] = []
    for raw in statements:
        sql = " ".join(raw.split())
        low = sql.lower()
        head = low.lstrip()
        if "project_editor_states" in low:
            if head.startswith("select"):
                editor_selects.append(sql)
            elif head.startswith("update"):
                editor_updates.append(sql)
            continue
        # 排除含 project_editor 的语句后，匹配 projects 表
        if re.search(r"\bprojects\b", low) and "project_editor" not in low:
            if head.startswith("update"):
                project_updates.append(sql)
            elif head.startswith("select"):
                project_selects.append(sql)
    return {
        "project_updates": project_updates,
        "project_selects": project_selects,
        "editor_selects": editor_selects,
        "editor_updates": editor_updates,
    }


def test_cas_single_project_lock_and_single_editor_state_read(client):
    """
    用途：expected + responseMatrixVersion 写路径仅一次 project 锁与一次 editor-state 锁后读取。
    二次开发：必须过滤目标表 SQL 并锁定次数；禁止只断言最终 200。
    """
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    matrix_v0 = base["responseMatrixVersion"]

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "project_editor_states" in low or re.search(r"\bprojects\b", low):
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    db = SessionLocal()
    try:
        result = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            expected_state_version=v0,
            response_matrix_version=matrix_v0,
            response_matrix=base["responseMatrix"],
            facts=[{"id": "fact_sql", "text": "一次锁读"}],
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db.close()

    assert result["facts"][0]["id"] == "fact_sql"
    assert result["stateVersion"] != v0
    assert result["stateVersion"] == _independent_state_version(result)

    classified = _classify_target_sql(captured)
    # 一次 projects 无副作用 UPDATE 作为写锁
    assert len(classified["project_updates"]) == 1, classified
    # 锁后仅一次 editor-state 读取；不得再 SELECT projects 或重复读 editor-state
    assert len(classified["editor_selects"]) == 1, classified
    assert len(classified["project_selects"]) == 0, classified


def test_cas_success_without_refresh_or_get_after_commit(client, monkeypatch):
    """
    用途：commit 后不得 refresh / 再次 get_editor_state；注入即抛错探针证明零调用。
    二次开发：旧实现会在 commit 后 refresh+GET，探针下必失败。
    """
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]

    def _boom_get(*args, **kwargs):
        raise RuntimeError("get_editor_state 不得在 CAS 写路径被调用")

    # 仅在 upsert 调用期间注入；验证持久化前必须恢复，避免污染后续 GET
    monkeypatch.setattr(editor_state_service, "get_editor_state", _boom_get)

    get_calls = {"n": 0}
    result: dict | None = None

    db = SessionLocal()
    try:
        real_db_get = db.get

        def _counting_get(entity, ident, **kwargs):
            # 额外读取探针：锁后第二次及以后对 editor-state 的 get 即失败
            table_name = ""
            if hasattr(entity, "__table__"):
                table_name = str(getattr(entity.__table__, "name", "") or "")
            name = table_name or getattr(entity, "__tablename__", None) or getattr(
                entity, "__name__", ""
            )
            if name in ("project_editor_states", "ProjectEditorStateRow"):
                get_calls["n"] += 1
                if get_calls["n"] > 1:
                    raise RuntimeError("锁后不得再次 db.get editor-state")
            return real_db_get(entity, ident, **kwargs)

        db.get = _counting_get  # type: ignore[method-assign]

        def _boom_refresh(*args, **kwargs):
            raise RuntimeError("commit 后不得调用 db.refresh")

        db.refresh = _boom_refresh  # type: ignore[method-assign]

        result = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            expected_state_version=v0,
            facts=[{"id": "fact_no_refresh", "text": "提交后零刷新"}],
        )
        assert result["facts"][0]["id"] == "fact_no_refresh"
        v1 = result["stateVersion"]
        _assert_state_version_format(v1)
        assert v1 != v0
        assert v1 == _independent_state_version(result)
        # 锁后仅允许一次 get（来自 _lock_for_versioned_write）
        assert get_calls["n"] == 1, get_calls
    finally:
        db.close()
        monkeypatch.undo()

    assert result is not None
    # 独立会话确认已持久化（探针已撤除）
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["facts"][0]["id"] == "fact_no_refresh"
    assert after["stateVersion"] == result["stateVersion"]


def test_commit_exception_rolls_back_and_persists_nothing(client):
    """
    用途：commit 抛错必须显式 rollback；新会话证明持久状态完全未改变；不得吞异常。
    """
    pid = _create_project(client)
    base = _seed_technical(client, pid)
    v0 = base["stateVersion"]
    facts_before = base["facts"]
    overview_before = base.get("analysisOverview")

    rollbacks = {"n": 0}
    db = SessionLocal()
    try:
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*args, **kwargs):
            raise RuntimeError("simulated commit failure")

        def _count_rollback(*args, **kwargs):
            rollbacks["n"] += 1
            return real_rollback(*args, **kwargs)

        db.commit = _bad_commit  # type: ignore[method-assign]
        db.rollback = _count_rollback  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            editor_state_service.upsert_editor_state(
                db,
                _WS,
                pid,
                expected_state_version=v0,
                facts=[{"id": "should_not_persist", "text": "不得落库"}],
                analysis_overview="不得落库概述",
            )
        assert rollbacks["n"] >= 1, "commit 异常必须触发显式 rollback"
        assert not db.in_transaction()
    finally:
        db.close()

    # 独立新会话：持久状态完全未改变
    db2 = SessionLocal()
    try:
        unchanged = editor_state_service.get_editor_state(db2, _WS, pid)
    finally:
        db2.close()
    assert unchanged["stateVersion"] == v0
    assert unchanged["facts"] == facts_before
    assert (unchanged.get("analysisOverview") or "") == (overview_before or "")
    assert unchanged["facts"][0]["id"] != "should_not_persist"

    via_api = client.get(f"/api/projects/{pid}/editor-state").json()
    assert via_api["stateVersion"] == v0
    assert via_api["facts"] == facts_before


# ---------- 10. updatedAt 稳定序列化 + 非有限数值兼容（P12B-A 第二次返修）----------


def test_upsert_updated_at_matches_immediate_independent_get(client):
    """
    用途：commit 前 upsert 响应的 updatedAt 与关闭会话后独立 GET 完全一致。
    二次开发：旧实现 commit 前为 aware isoformat（...+00:00），SQLite 重读为 naive，
    导致内容融合只读断言误判状态变化；stateVersion 不得仅因重读而改变。
    """
    pid = _create_project(client)
    db = SessionLocal()
    try:
        before = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            facts=[{"id": "fact_ts", "text": "时间戳稳定"}],
        )
    finally:
        db.close()

    assert before.get("updatedAt") is not None
    # 稳定规则：响应不得带时区后缀（与 SQLite 重读 naive 对齐）
    assert "+00:00" not in before["updatedAt"]
    assert before["updatedAt"].endswith("+00:00") is False

    db2 = SessionLocal()
    try:
        after = editor_state_service.get_editor_state(db2, _WS, pid)
    finally:
        db2.close()

    assert after["updatedAt"] == before["updatedAt"]
    assert after["stateVersion"] == before["stateVersion"]
    assert after["facts"] == before["facts"]
    _assert_state_version_format(after["stateVersion"])
    assert after["stateVersion"] == _independent_state_version(after)


def test_non_finite_floats_sanitized_on_write_and_version(client):
    """
    用途：service 写入嵌套 businessQuote 时 NaN/+Inf/-Inf 收敛为 None，有限值保持；
    返回与新会话 GET 一致；stateVersion 可严格 allow_nan=False 哈希；库中无字面量。
    二次开发：旧实现在 commit 前规范序列化时对 NaN/Inf 抛 ValueError，破坏 finance 兼容基线。
    """
    pid = _create_project(client, name="P12B非有限", kind="business")
    quote = {
        "rows": [
            {"item": "nan_row", "amount": float("nan")},
            {"item": "inf_row", "amount": float("inf")},
            {"item": "ninf_row", "amount": float("-inf")},
            {"item": "ok_row", "amount": 12.5},
        ],
        "notes": "非有限兼容",
    }

    db = SessionLocal()
    try:
        before = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            business_quote=quote,
        )
    finally:
        db.close()

    bq = before["businessQuote"]
    amounts = [row["amount"] for row in bq["rows"]]
    assert amounts == [None, None, None, 12.5]
    assert bq["notes"] == "非有限兼容"
    _assert_state_version_format(before["stateVersion"])
    assert before["stateVersion"] == _independent_state_version(before)

    db2 = SessionLocal()
    try:
        after = editor_state_service.get_editor_state(db2, _WS, pid)
        row = db2.get(ProjectEditorStateRow, pid)
        raw = row.business_json or "" if row is not None else ""
    finally:
        db2.close()

    after_amounts = [r["amount"] for r in after["businessQuote"]["rows"]]
    assert after_amounts == [None, None, None, 12.5]
    assert after["stateVersion"] == before["stateVersion"]
    assert after["stateVersion"] == _independent_state_version(after)
    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert "-Infinity" not in raw
    # 响应本身也可严格序列化（不得残留非有限 float）
    json.dumps(after, ensure_ascii=False, allow_nan=False)


def test_legacy_nonstandard_json_get_sanitizes_and_versions(client):
    """
    用途：绕过 service 手工写入 allow_nan=True 非标准 JSON 后，GET 必须收敛为 None
    并产出有效稳定 stateVersion；不得把 NaN/Infinity 字面量带进响应/哈希。
    二次开发：旧实现对存量非标准数值在 compute_full_state_version 时抛 ValueError。
    """
    pid = _create_project(client, name="P12B存量NaN", kind="business")
    seed = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "businessQuote": {
                "rows": [{"item": "seed", "amount": 1.0}],
                "notes": "seed",
            }
        },
    )
    assert seed.status_code == 200, seed.text

    poison = {
        "qualify": [],
        "toc": [],
        "quote": {
            "rows": [
                {"item": "legacy_nan", "amount": float("nan")},
                {"item": "legacy_inf", "amount": float("inf")},
                {"item": "legacy_ninf", "amount": float("-inf")},
                {"item": "legacy_ok", "amount": 3.25},
            ],
            "notes": "legacy-nonstandard",
        },
        "commit": [],
    }
    raw = json.dumps(poison, ensure_ascii=False, allow_nan=True)
    assert "NaN" in raw or "Infinity" in raw

    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, pid)
        assert row is not None
        row.business_json = raw
        db.commit()
    finally:
        db.close()

    db2 = SessionLocal()
    try:
        got = editor_state_service.get_editor_state(db2, _WS, pid)
    finally:
        db2.close()

    amounts = [r["amount"] for r in got["businessQuote"]["rows"]]
    assert amounts == [None, None, None, 3.25]
    assert got["businessQuote"]["notes"] == "legacy-nonstandard"
    _assert_state_version_format(got["stateVersion"])
    assert got["stateVersion"] == _independent_state_version(got)
    # 响应序列化不得含非标准字面量
    response_raw = json.dumps(got, ensure_ascii=False, allow_nan=False)
    assert "NaN" not in response_raw
    assert "Infinity" not in response_raw
