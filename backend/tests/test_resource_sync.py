"""
模块：受控外部资源同步测试
用途：验收签名清单同步、来源审计、幂等更新与 SSRF 输入拒绝，确保系统资源仍保持全局只读。
对接：resource_sync_service；/api/resources/sync-sources；资源中心本地读 API；pytest TestClient。
二次开发：新增发布协议或同步触发方式时，必须先补签名对象、来源隔离、事务回滚和脱敏输出回归，不得引入浏览器任意 URL 请求。
"""

import base64
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    ResourceRow,
    ResourceSyncItemRow,
    ResourceSyncRunRow,
    ResourceSyncSourceRow,
)
from app.services.resource_sync_service import (
    _apply_manifest,
    ManifestFetchResult,
    ResourceSyncError,
    ResourceSyncSourceConfig,
    parse_signed_manifest,
    sync_source,
    sync_configured_sources,
    validate_source_url,
)


def _source_config(private_key: Ed25519PrivateKey) -> ResourceSyncSourceConfig:
    """用途：构造测试发布源，公共密钥只存在于内存且不依赖真实外网地址。"""
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return ResourceSyncSourceConfig(
        id="official-guides",
        label="官方写作指南",
        manifest_url="https://publisher.example/resources.json",
        public_key=base64.b64encode(public_key).decode("ascii"),
    )


def _signed_manifest(
    private_key: Ed25519PrivateKey,
    *,
    version: int = 1,
    resources: list[dict] | None = None,
) -> bytes:
    """用途：按生产确定性 JSON 规则生成测试签名清单，避免测试与验签对象不一致。"""
    manifest = {
        "version": version,
        "resources": resources
        or [
            {
                "key": "technical-scoring-v1",
                "title": "技术标评分点响应写法",
                "description": "将评分表映射到技术方案正文。",
                "category": "写作指南",
                "tags": ["技术标", "评分"],
                "bodyMarkdown": "# 评分点\n\n逐条响应。",
                "tone": "violet",
            }
        ],
    }
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return json.dumps(
        {
            "manifest": manifest,
            "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii"),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _public_resolver(_host: str) -> tuple[str, ...]:
    """用途：为同步服务提供固定公共地址，避免测试触发真实 DNS 或网络连接。"""
    return ("8.8.8.8",)


def _fetcher(default_body: bytes, **overrides):
    """用途：构造受控下载结果，使同步事务测试与传输层测试相互独立。"""
    fields = {
        "status_code": 200,
        "content_type": "application/json; charset=utf-8",
        "content_encoding": "",
        "body": default_body,
    }
    fields.update(overrides)
    result = ManifestFetchResult(**fields)

    def fetch(_source, _resolved, *, max_bytes: int, timeout_seconds: int):
        assert max_bytes >= len(result.body)
        assert timeout_seconds > 0
        return result

    return fetch


def _sync(
    private_key: Ed25519PrivateKey,
    body: bytes,
    *,
    fetcher=None,
):
    """用途：调用同步服务并统一注入公共 DNS 结果与假下载器。"""
    db = SessionLocal()
    try:
        result = sync_source(
            db,
            _source_config(private_key),
            allowed_hosts={"publisher.example"},
            resolver=_public_resolver,
            fetcher=fetcher or _fetcher(body),
        )
        return result
    finally:
        db.close()


def test_signed_manifest_sync_is_idempotent_and_updates_existing_resource():
    private_key = Ed25519PrivateKey.generate()
    first_body = _signed_manifest(private_key)

    first = _sync(private_key, first_body)
    second = _sync(private_key, first_body)

    changed_body = _signed_manifest(
        private_key,
        version=2,
        resources=[
            {
                "key": "technical-scoring-v1",
                "title": "技术标评分点响应写法（更新）",
                "description": "更新后的摘要。",
                "category": "写作指南",
                "tags": ["技术标", "评分", "更新"],
                "bodyMarkdown": "# 评分点\n\n更新后的逐条响应。",
                "tone": "cyan",
            }
        ],
    )
    third = _sync(private_key, changed_body)

    assert (first.created, first.updated, first.skipped) == (1, 0, 0)
    assert (second.created, second.updated, second.skipped) == (0, 0, 1)
    assert (third.created, third.updated, third.skipped) == (0, 1, 0)

    db = SessionLocal()
    try:
        source = db.get(ResourceSyncSourceRow, "official-guides")
        assert source is not None
        assert source.last_manifest_version == 2
        item = db.scalar(select(ResourceSyncItemRow))
        assert item is not None
        resource = db.get(ResourceRow, item.resource_id)
        assert resource is not None
        assert resource.source == "system"
        assert resource.workspace_id is None
        assert resource.title.endswith("（更新）")
        assert resource.body_markdown.endswith("更新后的逐条响应。")
        assert len(db.scalars(select(ResourceSyncItemRow)).all()) == 1
    finally:
        db.close()


def test_invalid_signature_or_version_replay_keeps_existing_resource_and_writes_failed_run():
    private_key = Ed25519PrivateKey.generate()
    first_body = _signed_manifest(private_key)
    _sync(private_key, first_body)
    _sync(private_key, _signed_manifest(private_key, version=2))

    broken = json.loads(_signed_manifest(private_key, version=2).decode("utf-8"))
    broken["signature"] = "A" * 44
    with pytest.raises(ResourceSyncError, match="签名"):
        _sync(private_key, json.dumps(broken, ensure_ascii=False).encode("utf-8"))

    with pytest.raises(ResourceSyncError, match="版本"):
        _sync(private_key, _signed_manifest(private_key, version=1))

    db = SessionLocal()
    try:
        resource = db.scalar(select(ResourceRow).where(ResourceRow.id.like("res_sync_%")))
        assert resource is not None
        assert resource.body_markdown == "# 评分点\n\n逐条响应。"
        failed_runs = db.scalars(
            select(ResourceSyncRunRow).where(ResourceSyncRunRow.status == "failed")
        ).all()
        assert len(failed_runs) == 2
        assert all("https://" not in run.error_message for run in failed_runs)
    finally:
        db.close()


def test_oversized_signed_field_is_rejected_without_silent_truncation():
    private_key = Ed25519PrivateKey.generate()
    body = _signed_manifest(
        private_key,
        resources=[
            {
                "key": "too-long-title",
                "title": "超" * 501,
                "description": "",
                "category": "写作指南",
                "tags": [],
                "bodyMarkdown": "# 正文",
                "tone": "blue",
            }
        ],
    )

    with pytest.raises(ResourceSyncError, match="格式"):
        _sync(private_key, body)

    db = SessionLocal()
    try:
        assert db.scalars(select(ResourceRow).where(ResourceRow.id.like("res_sync_%"))).all() == []
    finally:
        db.close()


@pytest.mark.parametrize(
    "tags",
    [
        [f"tag-{index}" for index in range(21)],
        ["tag", "tag"],
        ["tag", ""],
        ["tag", " padded"],
    ],
)
def test_signed_tags_reject_silent_normalization_or_truncation(tags: list[str]):
    private_key = Ed25519PrivateKey.generate()
    body = _signed_manifest(
        private_key,
        resources=[
            {
                "key": "tag-policy",
                "title": "Tag policy",
                "description": "",
                "category": "guide",
                "tags": tags,
                "bodyMarkdown": "# Tag policy",
                "tone": "blue",
            }
        ],
    )

    with pytest.raises(ResourceSyncError):
        _sync(private_key, body)

    db = SessionLocal()
    try:
        assert db.scalars(select(ResourceRow).where(ResourceRow.id.like("res_sync_%"))).all() == []
    finally:
        db.close()


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("category", ""),
        ("category", " "),
        ("tone", ""),
        ("tone", " blue"),
    ],
)
def test_signed_optional_fields_reject_explicit_blank_or_trimmed_values(
    field_name: str, field_value: str
):
    private_key = Ed25519PrivateKey.generate()
    resource = {
        "key": "optional-policy",
        "title": "Optional policy",
        "description": "",
        "category": "guide",
        "tags": [],
        "bodyMarkdown": "# Optional policy",
        "tone": "blue",
    }
    resource[field_name] = field_value
    body = _signed_manifest(private_key, resources=[resource])

    with pytest.raises(ResourceSyncError):
        _sync(private_key, body)


def test_stale_sync_cannot_overwrite_newer_manifest_version():
    private_key = Ed25519PrivateKey.generate()

    def resource(version: int) -> dict:
        return {
            "key": "versioned-guide",
            "title": f"Guide v{version}",
            "description": "",
            "category": "guide",
            "tags": ["tag"],
            "bodyMarkdown": f"# Body v{version}",
            "tone": "blue",
        }

    _sync(private_key, _signed_manifest(private_key, version=2, resources=[resource(2)]))
    stale_db = SessionLocal()
    try:
        stale_source = stale_db.get(ResourceSyncSourceRow, "official-guides")
        assert stale_source is not None
        assert stale_source.last_manifest_version == 2
        stale_db.expunge(stale_source)
    finally:
        stale_db.close()

    _sync(private_key, _signed_manifest(private_key, version=4, resources=[resource(4)]))
    stale_manifest = parse_signed_manifest(
        _signed_manifest(private_key, version=3, resources=[resource(3)]),
        _source_config(private_key).public_key,
    )

    db = SessionLocal()
    try:
        run = ResourceSyncRunRow(
            id="rsr_stale_guard",
            source_id="official-guides",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        with pytest.raises(ResourceSyncError):
            _apply_manifest(db, stale_source, run, stale_manifest)
        db.rollback()
    finally:
        db.close()

    verify_db = SessionLocal()
    try:
        source = verify_db.get(ResourceSyncSourceRow, "official-guides")
        resource_row = verify_db.scalar(
            select(ResourceRow).where(ResourceRow.id.like("res_sync_%"))
        )
        assert source is not None
        assert source.last_manifest_version == 4
        assert resource_row is not None
        assert resource_row.body_markdown == "# Body v4"
    finally:
        verify_db.close()


@pytest.mark.parametrize(
    ("manifest_url", "allowed_hosts"),
    [
        ("http://publisher.example/resources.json", {"publisher.example"}),
        ("https://operator@publisher.example/resources.json", {"publisher.example"}),
        ("https://publisher.example:8443/resources.json", {"publisher.example"}),
        ("https://127.0.0.1/resources.json", {"127.0.0.1"}),
        ("https://other.example/resources.json", {"publisher.example"}),
    ],
)
def test_source_url_rejects_protocol_identity_port_and_host_bypasses(
    manifest_url: str, allowed_hosts: set[str]
):
    with pytest.raises(ResourceSyncError):
        validate_source_url(
            manifest_url,
            allowed_hosts=allowed_hosts,
            resolver=_public_resolver,
        )


def test_source_url_rejects_private_dns_before_fetching():
    with pytest.raises(ResourceSyncError, match="公共"):
        validate_source_url(
            "https://publisher.example/resources.json",
            allowed_hosts={"publisher.example"},
            resolver=lambda _host: ("127.0.0.1",),
        )


def test_empty_source_config_does_not_call_fetcher_or_open_network():
    settings = Settings(resource_sync_sources="[]")
    db = SessionLocal()
    try:
        result = sync_configured_sources(
            db,
            settings,
            fetcher=lambda *_args, **_kwargs: pytest.fail("空配置不应获取远端清单"),
        )
        assert result == []
    finally:
        db.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"status_code": 302},
        {"content_type": "text/html"},
        {"content_encoding": "gzip"},
        {"content_length": 2 * 1024 * 1024},
    ],
)
def test_transport_rejections_do_not_create_resources(overrides: dict):
    private_key = Ed25519PrivateKey.generate()
    body = _signed_manifest(private_key)
    fetcher = _fetcher(body, **overrides)

    with pytest.raises(ResourceSyncError):
        _sync(private_key, body, fetcher=fetcher)

    db = SessionLocal()
    try:
        assert db.scalars(select(ResourceRow).where(ResourceRow.id.like("res_sync_%"))).all() == []
        failed_run = db.scalar(
            select(ResourceSyncRunRow).where(ResourceSyncRunRow.status == "failed")
        )
        assert failed_run is not None
    finally:
        db.close()


def test_sync_source_status_api_hides_connection_and_key_details(client, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    config = _source_config(private_key)
    monkeypatch.setenv(
        "RESOURCE_SYNC_SOURCES",
        json.dumps(
            [
                {
                    "id": config.id,
                    "label": config.label,
                    "manifestUrl": config.manifest_url,
                    "publicKey": config.public_key,
                }
            ]
        ),
    )
    monkeypatch.setenv("RESOURCE_SYNC_ALLOWED_HOSTS", "publisher.example")
    get_settings.cache_clear()
    _sync(private_key, _signed_manifest(private_key))

    response = client.get("/api/resources/sync-sources")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "official-guides"
    assert data[0]["label"] == "官方写作指南"
    assert data[0]["lastStatus"] == "success"
    assert data[0]["lastSuccessAt"] is not None
    assert data[0]["lastAttemptedAt"] is not None
    assert data[0]["lastRun"] == {"created": 1, "updated": 0, "skipped": 0}
    serialized = json.dumps(data, ensure_ascii=False)
    assert "publisher.example" not in serialized
    assert config.public_key not in serialized


def test_sync_source_status_api_hides_invalid_configuration_details(client, monkeypatch):
    monkeypatch.setenv("RESOURCE_SYNC_SOURCES", "not-json")
    get_settings.cache_clear()

    response = client.get("/api/resources/sync-sources")

    assert response.status_code == 503
    assert response.json() == {"detail": "同步来源配置不可用"}
