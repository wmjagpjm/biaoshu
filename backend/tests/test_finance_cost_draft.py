"""
模块：P10C 财务成本草案定向测试
用途：验收成本条目 CRUD、分精度汇总、鉴权隔离、审计脱敏与 P10B 只读不变。
对接：app.api.finance 成本路由；app.services.finance_cost_service；deps.require_finance。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或依赖白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import AuthAuditEventRow, Workspace
from app.services import auth_service, editor_state_service, finance_cost_service, project_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_finance_p10c"
_OWNER_PASS = "TestPass-Finance-Cost-Owner-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-Finance-Cost-Role-0001!",
    "hr": "TestPass-Hr-Cost-Role-0001!",
    "bidder": "TestPass-Bidder-Cost-Role-0001!",
    "bid_writer": "TestPass-Writer-Cost-Role-0001!",
}
_DRAFT_KEYS = frozenset(
    {
        "projectId",
        "projectName",
        "quoteTotalFen",
        "costTotalFen",
        "grossProfitFen",
        "grossMarginBasisPoints",
        "costEntries",
    }
)
_ENTRY_KEYS = frozenset(
    {"id", "category", "name", "amountFen", "remark", "createdAt", "updatedAt"}
)
_FORBIDDEN_MARKERS = (
    "businessQualify",
    "businessToc",
    "businessCommit",
    "business_json",
    "createdByUserId",
    "created_by_user_id",
    "password",
    "csrf",
    "token_digest",
    "apiKey",
    "api_key",
    "SECRET_QUALIFY",
    "SECRET_TECH",
    "quoteRows",
    "quoteNotes",
)


@pytest.fixture
def required_settings(monkeypatch):
    """用途：切换 AUTH_MODE=required 并刷新配置缓存。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    """用途：required 模式下的 TestClient。"""
    with TestClient(app) as client:
        yield client


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
    db = SessionLocal()
    try:
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_OWNER_USER,
            password=_OWNER_PASS,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str):
    client.cookies.clear()
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def _owner_session(client: TestClient):
    _bootstrap()
    res = _login(client, _OWNER_USER, _OWNER_PASS)
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"], res.json()


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
):
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _login_finance(client: TestClient) -> str:
    """用途：创建 finance 成员并登录，返回 CSRF。"""
    csrf, _ = _owner_session(client)
    username = "user_finance_p10c"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS["finance"],
        role="finance",
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS["finance"])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10c"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _seed_projects(
    *,
    quote_rows: list[dict] | None = None,
    quote_amount: float | None = 128000.0,
) -> dict[str, str]:
    """
    用途：写入本空间商务标/技术标与跨空间商务标。
    返回：项目 id 字典。
    """
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_finance_p10c")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_finance_p10c",
                    name="其他财务空间P10C",
                    owner_user_id="user_other_finance_p10c",
                )
            )
            db.commit()

        rows = quote_rows
        if rows is None and quote_amount is not None:
            rows = [
                {
                    "id": "r1",
                    "name": "主机设备",
                    "unit": "套",
                    "quantity": "2",
                    "unitPrice": "50000",
                    "amount": float(quote_amount) - 28000.0 if quote_amount == 128000.0 else float(quote_amount),
                    "remark": "含安装",
                },
            ]
            if quote_amount == 128000.0:
                rows.append(
                    {
                        "id": "r2",
                        "name": "运维服务",
                        "unit": "年",
                        "quantity": "1",
                        "unitPrice": "28000",
                        "amount": 28000.0,
                        "remark": "",
                    }
                )

        biz = project_service.create_project(
            db,
            "ws_local",
            name="财务成本商务标",
            industry="能源",
            kind="business",
            status="draft",
        )
        tech = project_service.create_project(
            db,
            "ws_local",
            name="成本不可见技术标",
            industry="通用",
            kind="technical",
        )
        foreign = project_service.create_project(
            db,
            "ws_other_finance_p10c",
            name="跨空间成本商务标",
            industry="跨域",
            kind="business",
        )

        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            biz.id,
            business_qualify=[{"id": "q1", "title": "SECRET_QUALIFY"}],
            business_quote={
                "rows": rows or [],
                "notes": "仅财务可见备注",
            },
            parsed_markdown="SECRET_TECH 不得泄露",
        )
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            tech.id,
            business_quote={"rows": [{"id": "tr1", "amount": 1.0}], "notes": "tech"},
        )
        editor_state_service.upsert_editor_state(
            db,
            "ws_other_finance_p10c",
            foreign.id,
            business_quote={"rows": [{"id": "fr1", "amount": 9.0}], "notes": "foreign"},
        )
        return {
            "business_id": biz.id,
            "technical_id": tech.id,
            "foreign_id": foreign.id,
        }
    finally:
        db.close()


def _assert_no_leak(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"


def _cost_path(project_id: str, suffix: str = "cost-draft") -> str:
    return f"/api/finance/business-bids/{project_id}/{suffix}"


def test_yuan_to_fen_decimal_rounding_and_rejects():
    """报价金额仅用 Decimal 量化到分；非有限/非数值拒绝为 0。"""
    assert finance_cost_service.yuan_to_fen(128000.0) == 12_800_000
    assert finance_cost_service.yuan_to_fen(100.005) == 10001  # 半入
    assert finance_cost_service.yuan_to_fen(0) == 0
    assert finance_cost_service.yuan_to_fen(-12.3) == -1230
    assert finance_cost_service.yuan_to_fen(float("nan")) == 0
    assert finance_cost_service.yuan_to_fen(float("inf")) == 0
    assert finance_cost_service.yuan_to_fen("9000") == 0
    assert finance_cost_service.yuan_to_fen({"nested": 1}) == 0
    assert finance_cost_service.yuan_to_fen(True) == 0


def test_margin_basis_points_integer_math():
    """毛利率基点用整数/Decimal 最近值；报价<=0 时为 null。"""
    # 4450000/12800000*10000 = 3476.5625 → 3477
    assert finance_cost_service.gross_margin_basis_points(12_800_000, 4_450_000) == 3477
    assert finance_cost_service.gross_margin_basis_points(0, 0) is None
    assert finance_cost_service.gross_margin_basis_points(-100, 10) is None
    assert finance_cost_service.gross_margin_basis_points(10_000, -1_000) == -1000


def test_create_read_update_delete_and_summary(required_client):
    """财务可完成创建-读取-修改-删除；汇总与基点可复现。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    pid = ids["business_id"]
    headers = {"X-CSRF-Token": csrf}

    empty = required_client.get(_cost_path(pid))
    assert empty.status_code == 200, empty.text
    assert empty.headers.get("cache-control", "").lower() == "no-store"
    body = empty.json()
    assert set(body.keys()) == _DRAFT_KEYS
    assert body["projectId"] == pid
    assert body["projectName"] == "财务成本商务标"
    assert body["quoteTotalFen"] == 12_800_000
    assert body["costTotalFen"] == 0
    assert body["grossProfitFen"] == 12_800_000
    assert body["grossMarginBasisPoints"] == 10000
    assert body["costEntries"] == []
    _assert_no_leak(body)

    created = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={
            "category": "material",
            "name": "设备采购",
            "amountFen": 8_000_000,
            "remark": "主机与备件",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.headers.get("cache-control", "").lower() == "no-store"
    entry = created.json()
    assert set(entry.keys()) == _ENTRY_KEYS
    assert entry["category"] == "material"
    assert entry["name"] == "设备采购"
    assert entry["amountFen"] == 8_000_000
    assert entry["remark"] == "主机与备件"
    assert isinstance(entry["id"], str) and entry["id"]
    entry_id = entry["id"]
    _assert_no_leak(entry)

    created2 = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={
            "category": "labor",
            "name": "安装人工",
            "amountFen": 350_000,
            "remark": "",
        },
        headers=headers,
    )
    assert created2.status_code == 201, created2.text

    draft = required_client.get(_cost_path(pid))
    assert draft.status_code == 200
    dbody = draft.json()
    assert dbody["costTotalFen"] == 8_350_000
    assert dbody["grossProfitFen"] == 4_450_000
    assert dbody["grossMarginBasisPoints"] == 3477
    assert len(dbody["costEntries"]) == 2
    for item in dbody["costEntries"]:
        assert set(item.keys()) == _ENTRY_KEYS
    _assert_no_leak(dbody)

    patched = required_client.patch(
        f"{_cost_path(pid, 'cost-entries')}/{entry_id}",
        json={"amountFen": 9_000_000, "name": "设备采购(更新)"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["amountFen"] == 9_000_000
    assert patched.json()["name"] == "设备采购(更新)"
    assert patched.json()["id"] == entry_id

    after_patch = required_client.get(_cost_path(pid)).json()
    assert after_patch["costTotalFen"] == 9_350_000
    assert after_patch["grossProfitFen"] == 3_450_000

    deleted = required_client.delete(
        f"{_cost_path(pid, 'cost-entries')}/{entry_id}",
        headers=headers,
    )
    assert deleted.status_code == 204, deleted.text
    assert deleted.content in (b"", b"null", None) or deleted.text in ("", "null")

    after_del = required_client.get(_cost_path(pid)).json()
    assert after_del["costTotalFen"] == 350_000
    assert len(after_del["costEntries"]) == 1
    assert after_del["costEntries"][0]["name"] == "安装人工"


def test_zero_quote_negative_margin_and_no_entries(required_client):
    """报价为零/负、无条目、负毛利时基点与汇总正确。"""
    # 零报价
    zero_ids = _seed_projects(quote_rows=[])
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    zero = required_client.get(_cost_path(zero_ids["business_id"]))
    assert zero.status_code == 200
    z = zero.json()
    assert z["quoteTotalFen"] == 0
    assert z["costTotalFen"] == 0
    assert z["grossProfitFen"] == 0
    assert z["grossMarginBasisPoints"] is None
    assert z["costEntries"] == []

    # 负报价 + 成本 → 负毛利，基点 null
    neg_ids = _seed_projects(
        quote_rows=[{"id": "n1", "name": "负报价", "amount": -100.0}]
    )
    # 重新登录 finance（上一个仍有效）
    neg_draft = required_client.get(_cost_path(neg_ids["business_id"]))
    assert neg_draft.status_code == 200
    assert neg_draft.json()["quoteTotalFen"] == -10_000
    assert neg_draft.json()["grossMarginBasisPoints"] is None

    created = required_client.post(
        _cost_path(neg_ids["business_id"], "cost-entries"),
        json={"category": "other", "name": "杂费", "amountFen": 500, "remark": ""},
        headers=headers,
    )
    assert created.status_code == 201
    after = required_client.get(_cost_path(neg_ids["business_id"])).json()
    assert after["costTotalFen"] == 500
    assert after["grossProfitFen"] == -10_500
    assert after["grossMarginBasisPoints"] is None

    # 正报价但成本更高 → 负毛利，基点为负
    high_cost = _seed_projects(quote_amount=100.0)
    pid = high_cost["business_id"]
    required_client.post(
        _cost_path(pid, "cost-entries"),
        json={"category": "service", "name": "外包", "amountFen": 50_000, "remark": ""},
        headers=headers,
    )
    high = required_client.get(_cost_path(pid)).json()
    assert high["quoteTotalFen"] == 10_000
    assert high["costTotalFen"] == 50_000
    assert high["grossProfitFen"] == -40_000
    assert high["grossMarginBasisPoints"] == -40000


def test_validation_boundaries_reject_writes(required_client):
    """枚举/名称/备注/金额边界非法输入不得写入。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    path = _cost_path(ids["business_id"], "cost-entries")

    cases = [
        {"category": "tax", "name": "非法类", "amountFen": 1, "remark": ""},
        {"category": "labor", "name": "", "amountFen": 1, "remark": ""},
        {"category": "labor", "name": "x" * 121, "amountFen": 1, "remark": ""},
        {"category": "labor", "name": "ok", "amountFen": 0, "remark": ""},
        {"category": "labor", "name": "ok", "amountFen": -1, "remark": ""},
        {
            "category": "labor",
            "name": "ok",
            "amountFen": 1_000_000_000_000,
            "remark": "",
        },
        {"category": "labor", "name": "ok", "amountFen": 1, "remark": "r" * 501},
        {"category": "labor", "name": "ok", "amountFen": 1.5, "remark": ""},
    ]
    for payload in cases:
        res = required_client.post(path, json=payload, headers=headers)
        assert res.status_code in (400, 422), f"{payload} -> {res.status_code} {res.text}"

    # 合法边界：1 与最大分、name 120、remark 500
    ok = required_client.post(
        path,
        json={
            "category": "other",
            "name": "n" * 120,
            "amountFen": 999_999_999_999,
            "remark": "r" * 500,
        },
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    ok_min = required_client.post(
        path,
        json={"category": "labor", "name": "一", "amountFen": 1},
        headers=headers,
    )
    assert ok_min.status_code == 201, ok_min.text

    # PATCH 空体拒绝
    entry_id = ok.json()["id"]
    empty_patch = required_client.patch(
        f"{path}/{entry_id}",
        json={},
        headers=headers,
    )
    assert empty_patch.status_code in (400, 422)

    draft = required_client.get(_cost_path(ids["business_id"])).json()
    assert draft["costTotalFen"] == 999_999_999_999 + 1


def test_amount_fen_rejects_coerced_non_integers(required_client):
    """
    用途：amountFen 仅接受 JSON 整数；1.0 / true / \"1\" 在 POST 与 PATCH 均 422 且不写入。
    对接：FinanceCostEntryCreate / FinanceCostEntryUpdate 的 StrictInt 契约。
    """
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    path = _cost_path(ids["business_id"], "cost-entries")

    # 先写入一条合法条目，供 PATCH 与不写入断言基线
    seeded = required_client.post(
        path,
        json={
            "category": "labor",
            "name": "基线人工",
            "amountFen": 1000,
            "remark": "seed",
        },
        headers=headers,
    )
    assert seeded.status_code == 201, seeded.text
    entry_id = seeded.json()["id"]
    before = required_client.get(_cost_path(ids["business_id"])).json()
    before_total = before["costTotalFen"]
    before_count = len(before["costEntries"])
    before_entry = next(e for e in before["costEntries"] if e["id"] == entry_id)

    # JSON 中 1.0 / true / "1" 会被普通 int 强制为 1；StrictInt 必须拒绝
    coerced_values = [1.0, True, "1"]
    for bad in coerced_values:
        post_res = required_client.post(
            path,
            json={
                "category": "labor",
                "name": "强制转换拒绝",
                "amountFen": bad,
                "remark": "",
            },
            headers=headers,
        )
        assert post_res.status_code == 422, (
            f"POST amountFen={bad!r} -> {post_res.status_code} {post_res.text}"
        )

        patch_res = required_client.patch(
            f"{path}/{entry_id}",
            json={"amountFen": bad},
            headers=headers,
        )
        assert patch_res.status_code == 422, (
            f"PATCH amountFen={bad!r} -> {patch_res.status_code} {patch_res.text}"
        )

    after = required_client.get(_cost_path(ids["business_id"])).json()
    assert after["costTotalFen"] == before_total
    assert len(after["costEntries"]) == before_count
    after_entry = next(e for e in after["costEntries"] if e["id"] == entry_id)
    assert after_entry["amountFen"] == before_entry["amountFen"] == 1000
    assert after_entry["name"] == "基线人工"


def test_p10b_readonly_response_unchanged(required_client):
    """P10B 只读端点不得出现 P10C 成本字段。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    required_client.post(
        _cost_path(ids["business_id"], "cost-entries"),
        json={"category": "material", "name": "隐藏成本", "amountFen": 100, "remark": "密"},
        headers=headers,
    )

    listed = required_client.get("/api/finance/business-bids")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert "costEntries" not in item
    assert "costTotalFen" not in item
    assert "grossProfitFen" not in item
    assert "grossMarginBasisPoints" not in item
    assert "quoteTotalFen" not in item
    assert item["quoteTotal"] == 128000.0

    detail = required_client.get(f"/api/finance/business-bids/{ids['business_id']}")
    assert detail.status_code == 200
    dbody = detail.json()
    assert "costEntries" not in dbody
    assert "costTotalFen" not in dbody
    assert "grossProfitFen" not in dbody
    assert set(dbody.keys()) == {
        "projectId",
        "name",
        "industry",
        "status",
        "updatedAt",
        "quoteRowCount",
        "quoteTotal",
        "quoteRows",
        "quoteNotes",
    }


@pytest.mark.parametrize("role", ["bid_writer", "hr", "bidder"])
def test_non_finance_roles_forbidden_on_cost(required_client, role):
    """非 finance 角色访问成本草案固定 403。"""
    ids = _seed_projects()
    csrf = _login_role(required_client, role)
    headers = {"X-CSRF-Token": csrf}
    pid = ids["business_id"]

    get_res = required_client.get(_cost_path(pid))
    assert get_res.status_code == 403
    assert get_res.json()["detail"]["code"] == "role_forbidden"

    post_res = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={"category": "labor", "name": "x", "amountFen": 1},
        headers=headers,
    )
    assert post_res.status_code == 403
    assert post_res.json()["detail"]["code"] == "role_forbidden"


def test_owner_disabled_unauthenticated_denied(required_client, monkeypatch):
    """所有者、disabled、未登录均不得访问成本草案。"""
    ids = _seed_projects()
    csrf, _ = _owner_session(required_client)
    pid = ids["business_id"]
    owner_get = required_client.get(_cost_path(pid))
    assert owner_get.status_code == 403
    assert owner_get.json()["detail"]["code"] == "role_forbidden"
    owner_post = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={"category": "labor", "name": "x", "amountFen": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert owner_post.status_code == 403

    # disabled
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(_cost_path(pid))
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 未登录（库内管理员已由本测前置 owner_session 初始化）
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            res = client.get(_cost_path(pid))
            assert res.status_code in (401, 403)
            assert res.status_code != 200
    finally:
        get_settings.cache_clear()


def test_tech_foreign_missing_and_forged_entry_404(required_client):
    """技术标、跨空间、不存在项目/伪造条目统一 404 project_not_found。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = ids["business_id"]

    # 先在本空间创建一条
    created = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={"category": "labor", "name": "本空间", "amountFen": 10, "remark": ""},
        headers=headers,
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]

    for project_id in (ids["technical_id"], ids["foreign_id"], "proj_not_exist_p10c"):
        res = required_client.get(_cost_path(project_id))
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["code"] == "project_not_found"
        post = required_client.post(
            _cost_path(project_id, "cost-entries"),
            json={"category": "labor", "name": "x", "amountFen": 1},
            headers=headers,
        )
        assert post.status_code == 404
        assert post.json()["detail"]["code"] == "project_not_found"

    # 伪造条目 id
    forged = required_client.patch(
        f"{_cost_path(pid, 'cost-entries')}/fce_not_exist",
        json={"name": "hack"},
        headers=headers,
    )
    assert forged.status_code == 404
    assert forged.json()["detail"]["code"] == "project_not_found"

    forged_del = required_client.delete(
        f"{_cost_path(pid, 'cost-entries')}/fce_not_exist",
        headers=headers,
    )
    assert forged_del.status_code == 404
    assert forged_del.json()["detail"]["code"] == "project_not_found"

    # 条目存在但挂到错误项目路径
    wrong_project = required_client.patch(
        f"/api/finance/business-bids/{ids['technical_id']}/cost-entries/{entry_id}",
        json={"name": "hack"},
        headers=headers,
    )
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "project_not_found"

    # 本空间条目仍在
    still = required_client.get(_cost_path(pid)).json()
    assert len(still["costEntries"]) == 1
    assert still["costEntries"][0]["id"] == entry_id


def test_csrf_required_for_mutations(required_client):
    """POST/PATCH/DELETE 无 CSRF 或错误 CSRF 须拒绝。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    pid = ids["business_id"]
    path = _cost_path(pid, "cost-entries")

    no_csrf = required_client.post(
        path,
        json={"category": "labor", "name": "无csrf", "amountFen": 1},
    )
    assert no_csrf.status_code == 403

    bad = required_client.post(
        path,
        json={"category": "labor", "name": "坏csrf", "amountFen": 1},
        headers={"X-CSRF-Token": "definitely-wrong-csrf"},
    )
    assert bad.status_code == 403

    ok = required_client.post(
        path,
        json={"category": "labor", "name": "有csrf", "amountFen": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201
    entry_id = ok.json()["id"]

    patch_no = required_client.patch(
        f"{path}/{entry_id}",
        json={"name": "改"},
    )
    assert patch_no.status_code == 403

    del_no = required_client.delete(f"{path}/{entry_id}")
    assert del_no.status_code == 403


def test_audit_events_without_cost_body(required_client):
    """成功变更写审计：action 固定，target 仅条目 ID，不含金额/名称/备注。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = ids["business_id"]
    secret_name = "敏感成本名称XYZ"
    secret_remark = "敏感备注ABC999"

    created = required_client.post(
        _cost_path(pid, "cost-entries"),
        json={
            "category": "service",
            "name": secret_name,
            "amountFen": 12345,
            "remark": secret_remark,
        },
        headers=headers,
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]

    patched = required_client.patch(
        f"{_cost_path(pid, 'cost-entries')}/{entry_id}",
        json={"amountFen": 54321, "remark": "更新后备注"},
        headers=headers,
    )
    assert patched.status_code == 200

    deleted = required_client.delete(
        f"{_cost_path(pid, 'cost-entries')}/{entry_id}",
        headers=headers,
    )
    assert deleted.status_code == 204

    db = SessionLocal()
    try:
        events = (
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action.in_(
                    [
                        "finance_cost_create",
                        "finance_cost_update",
                        "finance_cost_delete",
                    ]
                )
            )
            .order_by(AuthAuditEventRow.created_at.asc())
            .all()
        )
        actions = [e.action for e in events]
        assert "finance_cost_create" in actions
        assert "finance_cost_update" in actions
        assert "finance_cost_delete" in actions
        for e in events:
            assert e.target == entry_id
            blob = json.dumps(
                {
                    "action": e.action,
                    "result": e.result,
                    "target": e.target,
                    "actor": e.actor_user_id,
                    "workspace": e.workspace_id,
                },
                ensure_ascii=False,
            )
            assert secret_name not in blob
            assert secret_remark not in blob
            assert "12345" not in blob
            assert "54321" not in blob
            assert "敏感" not in blob
            assert e.result == "success"
    finally:
        db.close()


def test_client_cannot_inject_ids_or_timestamps(required_client):
    """客户端不得指定 id/workspace/project/user/timestamp。"""
    ids = _seed_projects()
    csrf = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    res = required_client.post(
        _cost_path(ids["business_id"], "cost-entries"),
        json={
            "category": "labor",
            "name": "注入尝试",
            "amountFen": 100,
            "remark": "",
            "id": "fce_client",
            "workspaceId": "ws_other_finance_p10c",
            "projectId": ids["foreign_id"],
            "createdByUserId": "user_hacker",
            "createdAt": "2000-01-01T00:00:00+00:00",
            "updatedAt": "2000-01-01T00:00:00+00:00",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["id"] != "fce_client"
    assert body["createdAt"].startswith("2000") is False
    assert "createdByUserId" not in body
    assert body["name"] == "注入尝试"
