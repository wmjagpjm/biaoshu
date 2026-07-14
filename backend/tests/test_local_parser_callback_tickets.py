"""
模块：P8C 本地解析一次性回传票据定向测试
用途：验收 required 模式下签发权限、精确公开回调、原子单次消费、固定错误脱敏与事务回滚。
对接：parse_callback；auth_middleware；local_parser_ticket_service；entities.LocalParserCallbackTicketRow。
二次开发：仅固定合成口令与假票据；禁止外网、真实密钥、MinerU 启动或白名单外改动。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    LocalParserCallbackTicketRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    Workspace,
)
from app.services import auth_service, project_service
from app.services.local_parser_ticket_service import MAX_BODY_BYTES

# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_p8c_ticket"
_OWNER_PASS = "TestPass-P8C-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P8C-Writer-0001!",
    "finance": "TestPass-P8C-Finance-0001!",
    "hr": "TestPass-P8C-Hr-0001!",
    "bidder": "TestPass-P8C-Bidder-0001!",
}

_ISSUE_PATH = "/api/projects/{project_id}/parse-callback-ticket"
_PUBLIC_CALLBACK = "/api/local-parser/callback"
_OLD_CALLBACK = "/api/projects/{project_id}/parse-callback"

_ISSUE_KEYS = frozenset({"ticket", "expiresAt", "callbackPath"})
_SUCCESS_KEYS = frozenset({"ok", "chars", "taskId"})
_FAKE_TICKET = "p8c-test-fake-ticket-not-a-secret"
_SENSITIVE_MARKERS = (
    "SECRET_MARKDOWN_BODY_XYZ",
    "secret_file_name.pdf",
    "raw_ticket",
    "ticket_digest",
    _FAKE_TICKET,
    "X-Local-Parse-Ticket",
    "password",
    "csrfToken",
    "token_digest",
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


def _assert_no_sensitive(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker.lower() not in lower, f"泄漏敏感标记: {marker}"


def _ensure_bootstrap(role: str = auth_service.ROLE_BID_WRITER):
    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            return None
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


def _owner_session(client: TestClient, *, role: str = auth_service.ROLE_BID_WRITER):
    _ensure_bootstrap(role=role)
    res = _login(client, _OWNER_USER, _OWNER_PASS)
    assert res.status_code == 200, res.text
    body = res.json()
    return body["csrfToken"], body


def _create_member(client: TestClient, csrf: str, *, username: str, password: str, role: str):
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


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p8c"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
    )
    assert created.status_code in (201, 400, 409, 422), created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _create_project(
    *,
    name: str = "P8C技术标",
    kind: str = "technical",
    workspace_id: str = "ws_local",
) -> Project:
    db = SessionLocal()
    try:
        return project_service.create_project(
            db,
            workspace_id,
            name=name,
            kind=kind,
        )
    finally:
        db.close()


def _issue(client: TestClient, csrf: str, project_id: str, **kwargs):
    return client.post(
        _ISSUE_PATH.format(project_id=project_id),
        headers={"X-CSRF-Token": csrf},
        **kwargs,
    )


def _public_callback(
    client: TestClient,
    *,
    ticket: str | None,
    body: bytes | dict | None = None,
    extra_headers: dict | None = None,
    content_type: str = "application/json",
):
    headers = {}
    if ticket is not None:
        headers["X-Local-Parse-Ticket"] = ticket
    if extra_headers:
        headers.update(extra_headers)
    if body is None:
        payload = {
            "markdown": "# ok\n\nparsed content",
            "source": "mineru",
        }
        return client.post(_PUBLIC_CALLBACK, json=payload, headers=headers)
    if isinstance(body, (bytes, bytearray)):
        headers.setdefault("Content-Type", content_type)
        return client.post(_PUBLIC_CALLBACK, content=bytes(body), headers=headers)
    return client.post(_PUBLIC_CALLBACK, json=body, headers=headers)


def test_issue_endpoint_returns_201(required_client):
    """实现后签发端点必须精确 201，不得放宽为 404/405。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project()
    res = _issue(required_client, csrf, project.id)
    assert res.status_code == 201, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    assert set(res.json().keys()) == _ISSUE_KEYS


def test_strict_bid_writer_can_issue_and_roles_denied(required_client):
    """strict bid_writer 签发成功；finance/hr/bidder/仅 owner/disabled/未登录/跨空间拒绝。"""
    csrf = _login_role(required_client, "bid_writer")
    tech = _create_project(name="技术可签", kind="technical")
    biz = _create_project(name="商务可签", kind="business")

    ok = _issue(required_client, csrf, tech.id)
    assert ok.status_code == 201, ok.text
    assert ok.headers.get("cache-control", "").lower() == "no-store"
    body = ok.json()
    assert set(body.keys()) == _ISSUE_KEYS
    assert body["callbackPath"] == _PUBLIC_CALLBACK
    assert isinstance(body["ticket"], str) and len(body["ticket"]) >= 32
    expires = datetime.fromisoformat(body["expiresAt"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(minutes=9) < delta <= timedelta(minutes=10, seconds=30)

    ok_biz = _issue(required_client, csrf, biz.id)
    assert ok_biz.status_code == 201, ok_biz.text

    # 无 CSRF
    no_csrf = required_client.post(_ISSUE_PATH.format(project_id=tech.id))
    assert no_csrf.status_code == 403, no_csrf.text

    # 其他角色
    for role in ("finance", "hr", "bidder"):
        role_csrf = _login_role(required_client, role)
        denied = _issue(required_client, role_csrf, tech.id)
        assert denied.status_code == 403, f"{role}: {denied.text}"
        detail = denied.json().get("detail") or {}
        assert detail.get("code") == auth_service.CODE_ROLE_FORBIDDEN

    # 仅 owner（isOwner=true 且 role=bidder）不隐式绕过；须精确 bid_writer 角色
    owner_csrf, _owner_body = _owner_session(required_client)
    create_owner_bidder = required_client.post(
        "/api/auth/members",
        json={
            "username": "owner_bidder_p8c",
            "password": _ROLE_PASSWORDS["bidder"],
            "role": "bidder",
            "isOwner": True,
        },
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert create_owner_bidder.status_code in (201, 400, 409, 422), create_owner_bidder.text
    login_ob = _login(required_client, "owner_bidder_p8c", _ROLE_PASSWORDS["bidder"])
    assert login_ob.status_code == 200, login_ob.text
    ob_csrf = login_ob.json()["csrfToken"]
    owner_denied = _issue(required_client, ob_csrf, tech.id)
    assert owner_denied.status_code == 403, owner_denied.text

    # 未登录
    required_client.cookies.clear()
    anon = required_client.post(
        _ISSUE_PATH.format(project_id=tech.id),
        headers={"X-CSRF-Token": "x"},
    )
    assert anon.status_code in (401, 403), anon.text

    # 跨空间项目固定 404，响应不得回显 foreign_id
    other_ws = "ws_other_p8c"
    db = SessionLocal()
    try:
        if db.get(Workspace, other_ws) is None:
            db.add(
                Workspace(
                    id=other_ws,
                    name="他空间",
                    owner_user_id="user_other_p8c",
                )
            )
            db.commit()
        foreign = project_service.create_project(db, other_ws, name="跨空间项目")
        foreign_id = foreign.id
    finally:
        db.close()

    csrf = _login_role(required_client, "bid_writer")
    cross = _issue(required_client, csrf, foreign_id)
    assert cross.status_code == 404, cross.text
    _assert_no_sensitive(cross.json())
    assert foreign_id not in cross.text
    missing = _issue(required_client, csrf, "proj_not_exist_p8c")
    assert missing.status_code == 404, missing.text


def test_disabled_mode_cannot_issue(client):
    """AUTH_MODE=disabled 时 require_strict_bid_writer 拒绝签发。"""
    project = _create_project()
    res = client.post(_ISSUE_PATH.format(project_id=project.id))
    assert res.status_code == 403, res.text


def test_issue_stores_digest_only_and_audit_desensitized(required_client):
    """库内只存 SHA-256 摘要；签发审计固定脱敏；query/body 不能改 TTL。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project()
    res = _issue(
        required_client,
        csrf,
        project.id,
        params={"ttlMinutes": 999},
        json={"workspaceId": "ws_evil", "userId": "user_evil", "ttl": 1},
    )
    assert res.status_code == 201, res.text
    ticket = res.json()["ticket"]
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    db = SessionLocal()
    try:
        rows = list(db.scalars(select(LocalParserCallbackTicketRow)).all())
        assert len(rows) == 1
        row = rows[0]
        assert row.ticket_digest == digest
        assert ticket not in (row.ticket_digest or "")
        # 表字段必须精确等于契约八字段，不得多列
        cols = {c["name"] for c in inspect(db.bind).get_columns(row.__tablename__)}
        assert cols == {
            "id",
            "ticket_digest",
            "workspace_id",
            "project_id",
            "issued_by_user_id",
            "expires_at",
            "consumed_at",
            "created_at",
        }

        audits = list(
            db.scalars(
                select(AuthAuditEventRow).where(
                    AuthAuditEventRow.action == "local_parser_callback_ticket_issue"
                )
            ).all()
        )
        assert len(audits) == 1
        a = audits[0]
        assert a.result == "success"
        assert a.target == "single_project_10m"
        assert ticket not in json.dumps(
            {
                "action": a.action,
                "result": a.result,
                "target": a.target,
                "workspace": a.workspace_id,
                "actor": a.actor_user_id,
            },
            ensure_ascii=False,
        )
        assert digest not in (a.target or "")
        assert project.id not in (a.target or "")
    finally:
        db.close()


def test_public_callback_post_only_exact_path(required_client):
    """公共回调可无会话成功；其他 method/path 不公开。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project()
    issued = _issue(required_client, csrf, project.id)
    assert issued.status_code == 201, issued.text
    ticket = issued.json()["ticket"]

    # 无会话
    required_client.cookies.clear()
    ok = _public_callback(
        required_client,
        ticket=ticket,
        body={
            "markdown": "# 公共成功\n\n正文段落",
            "source": "mineru",
            "filename": "scan.pdf",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers.get("cache-control", "").lower() == "no-store"
    body = ok.json()
    assert set(body.keys()) == _SUCCESS_KEYS
    assert body["ok"] is True
    assert body["chars"] > 0
    assert isinstance(body["taskId"], str) and body["taskId"]
    assert "projectId" not in body
    assert "ticket" not in body

    # GET/PUT 精确路径仍需认证 → 401/403/405
    for method in ("get", "put"):
        res = getattr(required_client, method)(_PUBLIC_CALLBACK)
        assert res.status_code in (401, 403, 405), f"{method}: {res.status_code}"

    # 子路径不公开
    sub = required_client.post(f"{_PUBLIC_CALLBACK}/extra", json={"markdown": "x", "source": "mineru"})
    assert sub.status_code in (401, 403, 404), sub.text

    # 旧项目 callback 在 required 下无会话仍受保护
    old = required_client.post(
        _OLD_CALLBACK.format(project_id=project.id),
        json={"markdown": "old", "source": "mineru"},
    )
    assert old.status_code in (401, 403), old.text

    # 其他项目 API 不公开
    other = required_client.get(f"/api/projects/{project.id}")
    assert other.status_code in (401, 403), other.text


def test_ticket_invalid_unified_401(required_client):
    """缺失/错误/过期/重放/项目删除统一 401；X-Local-Token 不能替代。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project()
    issued = _issue(required_client, csrf, project.id)
    ticket = issued.json()["ticket"]

    required_client.cookies.clear()

    def _assert_invalid(res):
        assert res.status_code == 401, res.text
        detail = res.json().get("detail") or {}
        if isinstance(detail, dict):
            assert detail.get("code") == "local_parser_ticket_invalid"
        _assert_no_sensitive(res.json())

    # 缺失
    _assert_invalid(
        required_client.post(
            _PUBLIC_CALLBACK,
            json={"markdown": "m", "source": "mineru"},
        )
    )
    # 错误
    _assert_invalid(_public_callback(required_client, ticket=_FAKE_TICKET))
    # X-Local-Token 不替代
    _assert_invalid(
        required_client.post(
            _PUBLIC_CALLBACK,
            json={"markdown": "m", "source": "mineru"},
            headers={"X-Local-Token": ticket},
        )
    )

    # 成功一次
    first = _public_callback(
        required_client,
        ticket=ticket,
        body={"markdown": "# once\n\nok", "source": "mineru"},
    )
    assert first.status_code == 200, first.text
    # 重放
    _assert_invalid(
        _public_callback(
            required_client,
            ticket=ticket,
            body={"markdown": "# again\n\nok", "source": "mineru"},
        )
    )

    # 过期
    csrf = _login_role(required_client, "bid_writer")
    issued2 = _issue(required_client, csrf, project.id)
    ticket2 = issued2.json()["ticket"]
    digest2 = hashlib.sha256(ticket2.encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        row = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest2
            )
        ).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()
    required_client.cookies.clear()
    _assert_invalid(
        _public_callback(
            required_client,
            ticket=ticket2,
            body={"markdown": "# exp\n\nok", "source": "mineru"},
        )
    )

    # 项目删除后票据级联或统一 401
    csrf = _login_role(required_client, "bid_writer")
    doomed = _create_project(name="待删项目")
    issued3 = _issue(required_client, csrf, doomed.id)
    ticket3 = issued3.json()["ticket"]
    del_res = required_client.delete(
        f"/api/projects/{doomed.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert del_res.status_code in (200, 204), del_res.text
    required_client.cookies.clear()
    _assert_invalid(
        _public_callback(
            required_client,
            ticket=ticket3,
            body={"markdown": "# del\n\nok", "source": "mineru"},
        )
    )


def test_body_validation_fixed_errors(required_client):
    """body/字段边界与固定脱敏错误；敏感标记不得出现在响应。"""
    project = _create_project()

    def mint() -> str:
        csrf_local = _login_role(required_client, "bid_writer")
        res = _issue(required_client, csrf_local, project.id)
        assert res.status_code == 201, res.text
        ticket = res.json()["ticket"]
        required_client.cookies.clear()
        return ticket

    # JSON 非对象
    bad_arr = _public_callback(
        required_client,
        ticket=mint(),
        body=b'["not","object"]',
    )
    assert bad_arr.status_code == 400, bad_arr.text
    _assert_no_sensitive(bad_arr.json())

    # 额外键
    extra = _public_callback(
        required_client,
        ticket=mint(),
        body={
            "markdown": "ok",
            "source": "mineru",
            "evil": "SECRET_MARKDOWN_BODY_XYZ",
        },
    )
    assert extra.status_code == 400, extra.text
    assert "SECRET_MARKDOWN_BODY_XYZ" not in extra.text

    # source 非 mineru
    bad_src = _public_callback(
        required_client,
        ticket=mint(),
        body={"markdown": "ok", "source": "docling"},
    )
    assert bad_src.status_code == 400, bad_src.text

    # 非法 filename
    bad_name = _public_callback(
        required_client,
        ticket=mint(),
        body={
            "markdown": "ok",
            "source": "mineru",
            "filename": "../secret_file_name.pdf",
        },
    )
    assert bad_name.status_code == 400, bad_name.text
    assert "secret_file_name" not in bad_name.text

    # 空 markdown
    empty_md = _public_callback(
        required_client,
        ticket=mint(),
        body={"markdown": "   ", "source": "mineru"},
    )
    assert empty_md.status_code == 400, empty_md.text

    # 超长 markdown（码点，控制在 2 MiB 原始 body 内）
    huge = "x" * 1_000_001
    long_md = _public_callback(
        required_client,
        ticket=mint(),
        body={"markdown": huge, "source": "mineru"},
    )
    assert long_md.status_code == 400, long_md.text
    assert "xxxxx" not in long_md.text


def test_stream_body_limit_and_missing_ticket_before_body(required_client):
    """
    流式正文硬上限：恰好不超过继续字段校验；超过固定 413 且不反射正文。
    缺/空票据须在读正文前固定 401，即使携带超限/非法正文也不反射。
    """
    project = _create_project(name="流式上限项目")

    def mint() -> str:
        csrf_local = _login_role(required_client, "bid_writer")
        res = _issue(required_client, csrf_local, project.id)
        assert res.status_code == 201, res.text
        ticket = res.json()["ticket"]
        required_client.cookies.clear()
        return ticket

    prefix = b'{"markdown":"'
    suffix = b'","source":"docling"}'  # 非法 source，用于证明未超限时进入字段规则
    pad_len = MAX_BODY_BYTES - len(prefix) - len(suffix)
    assert pad_len > 0
    exact_body = prefix + (b"B" * pad_len) + suffix
    assert len(exact_body) == MAX_BODY_BYTES

    exact = _public_callback(required_client, ticket=mint(), body=exact_body)
    # 恰好上限：必须完整读入并按字段规则处理（非法 source → 400），不得误判 413
    assert exact.status_code == 400, exact.text
    detail = exact.json().get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("code") == "local_parser_callback_bad_request"
    assert "BBBB" not in exact.text
    assert "docling" not in exact.text

    # 超过上限：固定 413，响应不反射敏感正文
    oversize = (
        b'{"markdown":"SECRET_MARKDOWN_BODY_XYZ'
        + (b"A" * (MAX_BODY_BYTES))
        + b'","source":"mineru"}'
    )
    assert len(oversize) > MAX_BODY_BYTES
    too_big = _public_callback(required_client, ticket=mint(), body=oversize)
    assert too_big.status_code == 413, too_big.text
    detail413 = too_big.json().get("detail") or {}
    if isinstance(detail413, dict):
        assert detail413.get("code") == "local_parser_callback_payload_too_large"
    assert "SECRET_MARKDOWN_BODY_XYZ" not in too_big.text
    assert "AAAA" not in too_big.text

    # 缺票据 + 超限正文：须 401（先于正文读取），不反射正文
    missing_over = required_client.post(
        _PUBLIC_CALLBACK,
        content=oversize,
        headers={"Content-Type": "application/json"},
    )
    assert missing_over.status_code == 401, missing_over.text
    d_missing = missing_over.json().get("detail") or {}
    if isinstance(d_missing, dict):
        assert d_missing.get("code") == "local_parser_ticket_invalid"
    assert "SECRET_MARKDOWN_BODY_XYZ" not in missing_over.text
    assert "AAAA" not in missing_over.text

    # 空票据头 + 非法正文：同样先 401
    empty_ticket = required_client.post(
        _PUBLIC_CALLBACK,
        content=b'{"markdown":"SECRET_MARKDOWN_BODY_XYZ","source":"mineru","evil":1}',
        headers={
            "Content-Type": "application/json",
            "X-Local-Parse-Ticket": "   ",
        },
    )
    assert empty_ticket.status_code == 401, empty_ticket.text
    d_empty = empty_ticket.json().get("detail") or {}
    if isinstance(d_empty, dict):
        assert d_empty.get("code") == "local_parser_ticket_invalid"
    assert "SECRET_MARKDOWN_BODY_XYZ" not in empty_ticket.text


def test_success_writes_state_task_step_and_audit(required_client):
    """成功写 parsedMarkdown、唯一成功 task、项目步骤、固定审计。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project()
    ticket = _issue(required_client, csrf, project.id).json()["ticket"]
    required_client.cookies.clear()

    md = "# 解析结果正文\n\nSECRET_SHOULD_NOT_LEAK_IN_AUDIT"
    res = _public_callback(
        required_client,
        ticket=ticket,
        body={"markdown": md, "source": "mineru", "filename": "a.pdf"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == _SUCCESS_KEYS
    task_id = body["taskId"]

    db = SessionLocal()
    try:
        state = db.get(ProjectEditorStateRow, project.id)
        assert state is not None
        assert "解析结果正文" in (state.parsed_markdown or "")

        tasks = list(
            db.scalars(
                select(ProjectTaskRow).where(ProjectTaskRow.project_id == project.id)
            ).all()
        )
        assert len(tasks) == 1
        assert tasks[0].id == task_id
        assert tasks[0].type == "parse"
        assert tasks[0].status == "success"
        assert tasks[0].progress == 100

        proj = db.get(Project, project.id)
        assert proj is not None
        assert proj.status == "analyzing"
        assert proj.technical_plan_step == 1

        audits = list(
            db.scalars(
                select(AuthAuditEventRow).where(
                    AuthAuditEventRow.action == "local_parser_callback_apply"
                )
            ).all()
        )
        assert len(audits) == 1
        a = audits[0]
        assert a.result == "success"
        assert a.target == "one_time_ticket"
        blob = json.dumps(
            {
                "t": a.target,
                "a": a.action,
                "r": a.result,
                "w": a.workspace_id,
                "u": a.actor_user_id,
            },
            ensure_ascii=False,
        )
        assert "SECRET_SHOULD_NOT_LEAK_IN_AUDIT" not in blob
        assert ticket not in blob
        assert project.id not in (a.target or "")
        assert "a.pdf" not in blob
    finally:
        db.close()


def test_atomic_consume_and_midway_rollback(required_client, monkeypatch):
    """原子单次消费；monkeypatch 中途异常整体回滚。"""
    csrf = _login_role(required_client, "bid_writer")
    project = _create_project(name="回滚项目")
    ticket = _issue(required_client, csrf, project.id).json()["ticket"]
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    # 中途异常：原子消费后写入阶段抛错，整单事务回滚
    from app.services import local_parser_ticket_service as svc

    def boom(*args, **kwargs):
        raise RuntimeError("simulated-midway-failure")

    monkeypatch.setattr(svc, "_finalize_success_writes", boom)
    required_client.cookies.clear()
    res = _public_callback(
        required_client,
        ticket=ticket,
        body={"markdown": "# fail\n\nbody", "source": "mineru"},
    )
    # 路由应转为 500 或受控错误，但不得部分提交
    assert res.status_code >= 400

    db = SessionLocal()
    try:
        row = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        assert row.consumed_at is None
        state = db.get(ProjectEditorStateRow, project.id)
        assert state is None or not (state.parsed_markdown or "").strip()
        tasks = list(
            db.scalars(
                select(ProjectTaskRow).where(ProjectTaskRow.project_id == project.id)
            ).all()
        )
        assert tasks == []
        audits = list(
            db.scalars(
                select(AuthAuditEventRow).where(
                    AuthAuditEventRow.action == "local_parser_callback_apply"
                )
            ).all()
        )
        assert audits == []
    finally:
        db.close()

    # 恢复写入函数后做并发单次成功断言
    monkeypatch.undo()
    csrf = _login_role(required_client, "bid_writer")
    project2 = _create_project(name="并发项目")
    ticket2 = _issue(required_client, csrf, project2.id).json()["ticket"]
    required_client.cookies.clear()

    barrier = threading.Barrier(2)
    results: list[int] = []

    def worker():
        # 每个线程独立 client，共享 cookie 为空
        with TestClient(app) as c:
            barrier.wait()
            r = c.post(
                _PUBLIC_CALLBACK,
                json={"markdown": "# concurrent\n\nok", "source": "mineru"},
                headers={"X-Local-Parse-Ticket": ticket2},
            )
            results.append(r.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker), pool.submit(worker)]
        for f in futs:
            f.result()

    assert sorted(results) == [200, 401] or results.count(200) == 1
    assert results.count(200) == 1
    assert results.count(401) == 1

    db = SessionLocal()
    try:
        tasks = list(
            db.scalars(
                select(ProjectTaskRow).where(ProjectTaskRow.project_id == project2.id)
            ).all()
        )
        assert len(tasks) == 1
        assert tasks[0].status == "success"
        state = db.get(ProjectEditorStateRow, project2.id)
        assert state is not None
        assert "concurrent" in (state.parsed_markdown or "")
    finally:
        db.close()
