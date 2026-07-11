"""
模块：受控外部资源同步服务
用途：从服务端预配置、签名验证的 HTTPS JSON 清单同步全局只读 Markdown 资源，并记录来源、条目映射和脱敏运行审计。
对接：backend/scripts/sync_resources.py；GET /api/resources/sync-sources；Settings.RESOURCE_SYNC_*；ResourceRow。
二次开发：不得接受浏览器 URL、Token、Cookie、请求头或附件；新增发布协议前必须保留固定 IP TLS、签名验真、事务原子性和失败审计边界。
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import (
    ResourceRow,
    ResourceSyncItemRow,
    ResourceSyncRunRow,
    ResourceSyncSourceRow,
)

_SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,159}$")
_RESOURCE_TONES = frozenset({"blue", "violet", "cyan", "slate"})
_MAX_MANIFEST_ITEMS = 1000


class ResourceSyncError(Exception):
    """
    用途：以有限错误码中止同步，确保审计和命令输出不回显 URL、响应体、请求头或密钥。
    对接：sync_source；sync_resources.py；resources 路由的只读状态列表。
    二次开发：新增错误必须同时定义安全中文文案，禁止直接包装底层异常字符串。
    """

    _MESSAGES = {
        "config_invalid": "同步来源配置不合法",
        "source_url_invalid": "同步来源地址不合法",
        "source_host_denied": "同步来源主机未获白名单许可",
        "source_dns_denied": "同步来源未解析为公共地址",
        "fetch_failed": "同步来源连接失败",
        "fetch_status": "同步来源返回非成功状态",
        "fetch_content_type": "同步来源响应类型不受支持",
        "fetch_content_encoding": "同步来源响应编码不受支持",
        "fetch_too_large": "同步来源响应超过大小限制",
        "manifest_invalid": "同步清单格式不合法",
        "signature_invalid": "同步清单签名无效",
        "version_replay": "同步清单版本回退或内容不一致",
        "database_inconsistent": "同步资源映射不一致",
    }

    def __init__(self, code: str):
        self.code = code
        super().__init__(self._MESSAGES.get(code, "资源同步失败"))


@dataclass(frozen=True)
class ResourceSyncSourceConfig:
    """
    用途：承载单个受控发布源的非敏感配置，配置仅允许由服务端环境变量提供。
    对接：Settings.resource_sync_sources；sync_source；同步命令。
    二次开发：不得加入私钥、Token、Cookie、代理地址或客户端可控字段。
    """

    id: str
    label: str
    manifest_url: str
    public_key: str


@dataclass(frozen=True)
class ResolvedSource:
    """用途：保存已通过白名单和公共 IP 校验的来源地址，供固定 IP TLS 获取器使用。"""

    host: str
    request_target: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class ManifestFetchResult:
    """用途：抽象受限 HTTPS 获取结果，便于同步事务测试注入假下载器而不触发真实网络。"""

    status_code: int
    content_type: str
    content_encoding: str
    body: bytes
    content_length: int | None = None


@dataclass(frozen=True)
class SyncedResourcePayload:
    """用途：清单中单条资源的已校验写模型，不保留未知字段或任何连接信息。"""

    external_key: str
    title: str
    description: str
    category: str
    tags: tuple[str, ...]
    body_markdown: str
    tone: str
    content_hash: str


@dataclass(frozen=True)
class ParsedManifest:
    """用途：已完成验签、版本和字段校验的清单，作为写库前唯一可信输入。"""

    version: int
    manifest_hash: str
    resources: tuple[SyncedResourcePayload, ...]


@dataclass(frozen=True)
class ResourceSyncResult:
    """用途：返回单来源本次同步的可审计计数，供命令输出和只读状态 API 使用。"""

    source_id: str
    status: str
    created: int
    updated: int
    skipped: int


Resolver = Callable[[str], Iterable[str]]
Fetcher = Callable[[ResourceSyncSourceConfig, ResolvedSource], ManifestFetchResult]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decode_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ResourceSyncError("config_invalid") from exc
    if len(decoded) != 32:
        raise ResourceSyncError("config_invalid")
    return decoded


def _clean_string(value: Any, *, limit: int, default: str = "") -> str:
    if not isinstance(value, str):
        raise ResourceSyncError("manifest_invalid")
    normalized = value.strip()
    # 已签名内容不能像用户手填字段那样静默截断或修剪，否则本地资源与发布方签名的事实不再一致。
    if len(normalized) > limit or "\x00" in normalized or normalized != value:
        raise ResourceSyncError("manifest_invalid")
    return normalized or default


def _clean_body_markdown(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ResourceSyncError("manifest_invalid")
    if len(value) > limit or "\x00" in value or not value.strip():
        raise ResourceSyncError("manifest_invalid")
    return value


def _parse_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ResourceSyncError("manifest_invalid")
    if len(value) > 20:
        raise ResourceSyncError("manifest_invalid")
    tags: list[str] = []
    for item in value:
        tag = _clean_string(item, limit=60)
        if not tag or tag in tags:
            raise ResourceSyncError("manifest_invalid")
        tags.append(tag)
    return tuple(tags)


def parse_configured_sources(raw_sources: str) -> tuple[ResourceSyncSourceConfig, ...]:
    """
    用途：解析服务端 JSON 来源配置，并拒绝重复 ID、空字段和不合法 Ed25519 公钥。
    对接：Settings.resource_sync_sources；管理员同步命令；同步状态读取接口。
    二次开发：来源来源只能是环境变量或未来受鉴权的管理配置，不得改为请求参数。
    """
    try:
        data = json.loads(raw_sources or "[]")
    except json.JSONDecodeError as exc:
        raise ResourceSyncError("config_invalid") from exc
    if not isinstance(data, list):
        raise ResourceSyncError("config_invalid")
    configs: list[ResourceSyncSourceConfig] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, Mapping):
            raise ResourceSyncError("config_invalid")
        source_id = item.get("id")
        label = item.get("label")
        manifest_url = item.get("manifestUrl")
        public_key = item.get("publicKey")
        if not all(isinstance(value, str) for value in (source_id, label, manifest_url, public_key)):
            raise ResourceSyncError("config_invalid")
        source_id = source_id.strip()
        label = label.strip()
        if (
            not _SOURCE_ID_PATTERN.fullmatch(source_id)
            or not label
            or len(label) > 200
            or source_id in seen
        ):
            raise ResourceSyncError("config_invalid")
        _decode_public_key(public_key)
        configs.append(
            ResourceSyncSourceConfig(
                id=source_id,
                label=label,
                manifest_url=manifest_url.strip(),
                public_key=public_key.strip(),
            )
        )
        seen.add(source_id)
    return tuple(configs)


def configured_sources(settings: Settings) -> tuple[ResourceSyncSourceConfig, ...]:
    """
    用途：从 Settings 读取受控来源，配置为空时返回空元组且不产生出站连接。
    对接：sync_resources.py；GET /api/resources/sync-sources；RESOURCE_SYNC_SOURCES。
    """
    return parse_configured_sources(settings.resource_sync_sources)


def _default_resolver(host: str) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ResourceSyncError("source_dns_denied") from exc
    return tuple(dict.fromkeys(item[4][0] for item in results))


def validate_source_url(
    manifest_url: str,
    *,
    allowed_hosts: set[str],
    resolver: Resolver = _default_resolver,
) -> ResolvedSource:
    """
    用途：在出站前验证 HTTPS 地址、精确主机白名单和全部 DNS 公网解析结果，构造不可二次解析的目标。
    对接：sync_source；RESOURCE_SYNC_ALLOWED_HOSTS；固定 IP TLS 获取器。
    二次开发：不得降级为前缀白名单、通配符、重定向跟随或 httpx 的默认 DNS 重解析。
    """
    try:
        parsed = urlsplit(manifest_url)
        port = parsed.port
    except ValueError as exc:
        raise ResourceSyncError("source_url_invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ResourceSyncError("source_url_invalid")
    host = parsed.hostname.lower().rstrip(".")
    if not host:
        raise ResourceSyncError("source_url_invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ResourceSyncError("source_url_invalid")
    if host not in {item.lower().rstrip(".") for item in allowed_hosts}:
        raise ResourceSyncError("source_host_denied")
    try:
        addresses = tuple(dict.fromkeys(str(value) for value in resolver(host)))
    except ResourceSyncError:
        raise
    except Exception as exc:
        raise ResourceSyncError("source_dns_denied") from exc
    if not addresses:
        raise ResourceSyncError("source_dns_denied")
    try:
        parsed_ips = tuple(ipaddress.ip_address(value) for value in addresses)
    except ValueError as exc:
        raise ResourceSyncError("source_dns_denied") from exc
    if any(not address.is_global for address in parsed_ips):
        raise ResourceSyncError("source_dns_denied")
    path = parsed.path or "/"
    request_target = f"{path}?{parsed.query}" if parsed.query else path
    return ResolvedSource(host=host, request_target=request_target, addresses=addresses)


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    """用途：连接已校验 IP，但用原主机名作 TLS SNI 与证书校验，避免下载阶段 DNS 重绑定。"""

    def __init__(self, host: str, address: str, timeout_seconds: int):
        super().__init__(
            host=host,
            port=443,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _default_fetcher(
    source: ResourceSyncSourceConfig,
    resolved: ResolvedSource,
    *,
    max_bytes: int,
    timeout_seconds: int,
) -> ManifestFetchResult:
    """
    用途：通过固定公共 IP 发起单次 HTTPS GET；禁重定向、禁代理和压缩编码，并限制响应体。
    对接：sync_source；ResolvedSource；RESOURCE_SYNC_MAX_BYTES/TIMEOUT_SECONDS。
    二次开发：若替换 HTTP 客户端，必须保留 IP 固定、TLS SNI、证书校验和不重试内部地址的语义。
    """
    last_error: OSError | None = None
    for address in resolved.addresses:
        connection = _PinnedHttpsConnection(resolved.host, address, timeout_seconds)
        try:
            connection.request(
                "GET",
                resolved.request_target,
                headers={
                    "Host": resolved.host,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "biaoshu-resource-sync/1",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            content_length_text = response.getheader("Content-Length")
            content_length = (
                int(content_length_text)
                if content_length_text and content_length_text.isdecimal()
                else None
            )
            if content_length is not None and content_length > max_bytes:
                raise ResourceSyncError("fetch_too_large")
            body = response.read(max_bytes + 1)
            return ManifestFetchResult(
                status_code=response.status,
                content_type=response.getheader("Content-Type", ""),
                content_encoding=response.getheader("Content-Encoding", ""),
                body=body,
                content_length=content_length,
            )
        except ResourceSyncError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            last_error = exc
        finally:
            connection.close()
    raise ResourceSyncError("fetch_failed") from last_error


def _validate_fetch_result(result: ManifestFetchResult, max_bytes: int) -> None:
    if result.status_code != 200:
        raise ResourceSyncError("fetch_status")
    if result.content_type.split(";", 1)[0].strip().casefold() != "application/json":
        raise ResourceSyncError("fetch_content_type")
    if result.content_encoding.strip().casefold() not in ("", "identity"):
        raise ResourceSyncError("fetch_content_encoding")
    if result.content_length is not None and result.content_length > max_bytes:
        raise ResourceSyncError("fetch_too_large")
    if len(result.body) > max_bytes:
        raise ResourceSyncError("fetch_too_large")


def _parse_manifest_resource(value: Any) -> SyncedResourcePayload:
    if not isinstance(value, Mapping):
        raise ResourceSyncError("manifest_invalid")
    allowed_fields = {
        "key",
        "title",
        "description",
        "category",
        "tags",
        "bodyMarkdown",
        "tone",
    }
    if set(value) - allowed_fields:
        raise ResourceSyncError("manifest_invalid")
    external_key = _clean_string(value.get("key"), limit=160)
    title = _clean_string(value.get("title"), limit=500)
    body_markdown = _clean_body_markdown(value.get("bodyMarkdown"), limit=100000)
    if (
        not external_key
        or not _RESOURCE_KEY_PATTERN.fullmatch(external_key)
        or not title
    ):
        raise ResourceSyncError("manifest_invalid")
    description = _clean_string(value.get("description", ""), limit=2000)
    category = "资源"
    if "category" in value:
        category = _clean_string(value.get("category"), limit=100)
        if not category:
            raise ResourceSyncError("manifest_invalid")
    tags = _parse_tags(value.get("tags", []))
    tone = "blue"
    if "tone" in value:
        tone = _clean_string(value.get("tone"), limit=16)
        if not tone:
            raise ResourceSyncError("manifest_invalid")
    if tone not in _RESOURCE_TONES:
        raise ResourceSyncError("manifest_invalid")
    normalized = {
        "key": external_key,
        "title": title,
        "description": description,
        "category": category,
        "tags": tags,
        "bodyMarkdown": body_markdown,
        "tone": tone,
    }
    return SyncedResourcePayload(
        external_key=external_key,
        title=title,
        description=description,
        category=category,
        tags=tags,
        body_markdown=body_markdown,
        tone=tone,
        content_hash=_sha256(_canonical_json(normalized)),
    )


def parse_signed_manifest(body: bytes, public_key: str) -> ParsedManifest:
    """
    用途：验证清单信封的 Ed25519 签名、版本与资源字段，并在写库前移除未知输入字段。
    对接：sync_source；发布方 JSON 清单协议；ResourceSyncSourceConfig.public_key。
    二次开发：签名对象必须持续是 manifest 的确定性 JSON；更换协议须新增版本而非放宽当前解析。
    """
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceSyncError("manifest_invalid") from exc
    if not isinstance(envelope, Mapping) or set(envelope) != {"manifest", "signature"}:
        raise ResourceSyncError("manifest_invalid")
    manifest = envelope.get("manifest")
    signature = envelope.get("signature")
    if not isinstance(manifest, Mapping) or not isinstance(signature, str):
        raise ResourceSyncError("manifest_invalid")
    try:
        signature_bytes = base64.b64decode(signature.encode("ascii"), validate=True)
        verifier = Ed25519PublicKey.from_public_bytes(_decode_public_key(public_key))
        verifier.verify(signature_bytes, _canonical_json(manifest))
    except (UnicodeEncodeError, ValueError, InvalidSignature) as exc:
        raise ResourceSyncError("signature_invalid") from exc
    if set(manifest) != {"version", "resources"}:
        raise ResourceSyncError("manifest_invalid")
    version = manifest.get("version")
    resource_values = manifest.get("resources")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version <= 0
        or not isinstance(resource_values, list)
        or not resource_values
        or len(resource_values) > _MAX_MANIFEST_ITEMS
    ):
        raise ResourceSyncError("manifest_invalid")
    resources = tuple(_parse_manifest_resource(value) for value in resource_values)
    if len({resource.external_key for resource in resources}) != len(resources):
        raise ResourceSyncError("manifest_invalid")
    return ParsedManifest(
        version=version,
        manifest_hash=_sha256(_canonical_json(manifest)),
        resources=resources,
    )


def _source_fingerprint(public_key: str) -> str:
    return _sha256(_decode_public_key(public_key))


def _validate_source_config(source_config: ResourceSyncSourceConfig) -> None:
    """用途：防御内部调用绕过环境变量解析时写入异常来源 ID、标签或公钥。"""
    if (
        not _SOURCE_ID_PATTERN.fullmatch(source_config.id)
        or not source_config.label.strip()
        or len(source_config.label.strip()) > 200
        or not source_config.manifest_url.strip()
    ):
        raise ResourceSyncError("config_invalid")
    _decode_public_key(source_config.public_key)


def _upsert_source(
    db: Session, source_config: ResourceSyncSourceConfig
) -> ResourceSyncSourceRow:
    now = _now()
    row = db.get(ResourceSyncSourceRow, source_config.id)
    if row is None:
        row = ResourceSyncSourceRow(
            id=source_config.id,
            label=source_config.label,
            manifest_url=source_config.manifest_url,
            public_key_fingerprint=_source_fingerprint(source_config.public_key),
            last_status="never",
            last_attempted_at=now,
        )
        db.add(row)
    else:
        row.label = source_config.label
        row.manifest_url = source_config.manifest_url
        row.public_key_fingerprint = _source_fingerprint(source_config.public_key)
        row.last_attempted_at = now
    return row


def _record_failure(db: Session, run_id: str, source_id: str, error: ResourceSyncError) -> None:
    run = db.get(ResourceSyncRunRow, run_id)
    source = db.get(ResourceSyncSourceRow, source_id)
    now = _now()
    if run is not None:
        run.status = "failed"
        run.error_code = error.code
        run.error_message = str(error)
        run.finished_at = now
    if source is not None:
        source.last_status = "failed"
        source.last_attempted_at = now
    db.commit()


def _apply_manifest(
    db: Session,
    source: ResourceSyncSourceRow,
    run: ResourceSyncRunRow,
    manifest: ParsedManifest,
) -> ResourceSyncResult:
    if source.last_manifest_version:
        if manifest.version < source.last_manifest_version:
            raise ResourceSyncError("version_replay")
        if (
            manifest.version == source.last_manifest_version
            and manifest.manifest_hash != source.last_manifest_hash
        ):
            raise ResourceSyncError("version_replay")
    now = _now()
    created = 0
    updated = 0
    skipped = 0
    for payload in manifest.resources:
        item = db.scalar(
            select(ResourceSyncItemRow).where(
                ResourceSyncItemRow.source_id == source.id,
                ResourceSyncItemRow.external_key == payload.external_key,
            )
        )
        if item is None:
            resource = ResourceRow(
                id=_new_id("res_sync"),
                workspace_id=None,
                source="system",
                title=payload.title,
                description=payload.description,
                category=payload.category,
                tags_json=json.dumps(payload.tags, ensure_ascii=False),
                body_markdown=payload.body_markdown,
                tone=payload.tone,
                view_count=0,
                created_at=now,
                updated_at=now,
            )
            db.add(resource)
            db.flush()
            db.add(
                ResourceSyncItemRow(
                    id=_new_id("rsi"),
                    source_id=source.id,
                    external_key=payload.external_key,
                    resource_id=resource.id,
                    content_hash=payload.content_hash,
                    last_seen_at=now,
                )
            )
            created += 1
            continue
        resource = db.get(ResourceRow, item.resource_id)
        if resource is None or resource.source != "system" or resource.workspace_id is not None:
            raise ResourceSyncError("database_inconsistent")
        item.last_seen_at = now
        if item.content_hash == payload.content_hash:
            skipped += 1
            continue
        resource.title = payload.title
        resource.description = payload.description
        resource.category = payload.category
        resource.tags_json = json.dumps(payload.tags, ensure_ascii=False)
        resource.body_markdown = payload.body_markdown
        resource.tone = payload.tone
        resource.updated_at = now
        item.content_hash = payload.content_hash
        updated += 1
    guarded_update = db.execute(
        update(ResourceSyncSourceRow)
        .where(
            ResourceSyncSourceRow.id == source.id,
            or_(
                ResourceSyncSourceRow.last_manifest_version.is_(None),
                ResourceSyncSourceRow.last_manifest_version < manifest.version,
                and_(
                    ResourceSyncSourceRow.last_manifest_version == manifest.version,
                    ResourceSyncSourceRow.last_manifest_hash == manifest.manifest_hash,
                ),
            ),
        )
        .values(
            last_manifest_version=manifest.version,
            last_manifest_hash=manifest.manifest_hash,
            last_status="success",
            last_attempted_at=now,
            last_success_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if guarded_update.rowcount != 1:
        raise ResourceSyncError("version_replay")
    run.status = "success"
    run.created_count = created
    run.updated_count = updated
    run.skipped_count = skipped
    run.finished_at = now
    return ResourceSyncResult(
        source_id=source.id,
        status="success",
        created=created,
        updated=updated,
        skipped=skipped,
    )


def sync_source(
    db: Session,
    source_config: ResourceSyncSourceConfig,
    *,
    allowed_hosts: set[str],
    max_bytes: int = 1024 * 1024,
    timeout_seconds: int = 10,
    resolver: Resolver = _default_resolver,
    fetcher: Callable[..., ManifestFetchResult] = _default_fetcher,
) -> ResourceSyncResult:
    """
    用途：同步单个受控发布源；仅在完整验签和版本检查后原子更新资源，失败时保留脱敏运行审计且零写入资源。
    对接：管理员同步命令；ResourceSyncSourceRow/RunRow/ItemRow；Settings 受控配置。
    二次开发：批量调度应逐源调用本函数；不得把 fetcher 替换为接受请求 URL、代理或认证头的实现。
    """
    if max_bytes <= 0 or timeout_seconds <= 0:
        raise ResourceSyncError("config_invalid")
    _validate_source_config(source_config)
    source = _upsert_source(db, source_config)
    # 审计运行依赖来源外键；先刷入来源，避免无 relationship 的独立 ORM 行出现插入顺序歧义。
    db.flush()
    run = ResourceSyncRunRow(
        id=_new_id("rsr"),
        source_id=source_config.id,
        status="running",
        started_at=_now(),
    )
    db.add(run)
    db.commit()
    try:
        resolved = validate_source_url(
            source_config.manifest_url,
            allowed_hosts=allowed_hosts,
            resolver=resolver,
        )
        fetched = fetcher(
            source_config,
            resolved,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        _validate_fetch_result(fetched, max_bytes)
        manifest = parse_signed_manifest(fetched.body, source_config.public_key)
        source = db.get(ResourceSyncSourceRow, source_config.id)
        run = db.get(ResourceSyncRunRow, run.id)
        if source is None or run is None:
            raise ResourceSyncError("database_inconsistent")
        result = _apply_manifest(db, source, run, manifest)
        db.commit()
        return result
    except ResourceSyncError as exc:
        db.rollback()
        _record_failure(db, run.id, source_config.id, exc)
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        safe_error = ResourceSyncError("database_inconsistent")
        _record_failure(db, run.id, source_config.id, safe_error)
        raise safe_error from exc
    except Exception as exc:
        db.rollback()
        safe_error = ResourceSyncError("fetch_failed")
        _record_failure(db, run.id, source_config.id, safe_error)
        raise safe_error from exc


def sync_configured_sources(
    db: Session,
    settings: Settings,
    *,
    resolver: Resolver = _default_resolver,
    fetcher: Callable[..., ManifestFetchResult] = _default_fetcher,
) -> list[ResourceSyncResult]:
    """
    用途：依次同步当前服务端配置的所有来源，空配置返回空列表且不发起网络请求。
    对接：backend/scripts/sync_resources.py；Settings；sync_source。
    二次开发：应用内定时任务应在有管理员鉴权和队列审计后另建，不得在请求路径直接调用。
    """
    sources = configured_sources(settings)
    allowed_hosts = settings.resource_sync_allowed_host_set()
    return [
        sync_source(
            db,
            source,
            allowed_hosts=allowed_hosts,
            max_bytes=settings.resource_sync_max_bytes,
            timeout_seconds=settings.resource_sync_timeout_seconds,
            resolver=resolver,
            fetcher=fetcher,
        )
        for source in sources
    ]


def list_sync_source_statuses(
    db: Session, settings: Settings
) -> list[dict[str, Any]]:
    """
    用途：返回当前配置来源的脱敏只读状态，供资源页面或未来管理员界面展示同步健康度。
    对接：GET /api/resources/sync-sources；configured_sources；ResourceSyncRunRow。
    二次开发：不得返回 manifest_url、公共密钥、原始错误或远端正文；同步触发仍必须保留在管理员命令。
    """
    sources = configured_sources(settings)
    rows: list[dict[str, Any]] = []
    for config in sources:
        source = db.get(ResourceSyncSourceRow, config.id)
        if source is None:
            rows.append(
                {
                    "id": config.id,
                    "label": config.label,
                    "last_status": "never",
                    "last_success_at": None,
                    "last_attempted_at": None,
                    "last_run": None,
                }
            )
            continue
        run = db.scalar(
            select(ResourceSyncRunRow)
            .where(ResourceSyncRunRow.source_id == config.id)
            .order_by(ResourceSyncRunRow.started_at.desc())
            .limit(1)
        )
        rows.append(
            {
                "id": source.id,
                "label": config.label,
                "last_status": source.last_status,
                "last_success_at": source.last_success_at,
                "last_attempted_at": source.last_attempted_at,
                "last_run": (
                    {
                        "created": run.created_count,
                        "updated": run.updated_count,
                        "skipped": run.skipped_count,
                    }
                    if run is not None and run.status == "success"
                    else None
                ),
            }
        )
    return rows
