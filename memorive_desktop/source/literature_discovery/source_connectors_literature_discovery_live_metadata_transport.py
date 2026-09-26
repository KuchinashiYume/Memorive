"""DISCOVERY-ACCEPTANCE bounded live transport for public scholarly metadata only.

The module deliberately contains no discovery, identity, ranking, Card, or
promotion logic.  It accepts an already frozen request and a Schema-valid
pre-send receipt, performs at most one HTTPS GET through an injected opener,
persists the response before parsing, and returns a terminal receipt.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from jsonschema import Draft202012Validator, FormatChecker


RECEIPT_SCHEMA_VERSION = "discovery.data_contracts.external-source-request-receipt.0.2"
ALLOWED_ENDPOINTS = {
    "ARXIV": {
        "endpoint_family": "ARXIV_QUERY",
        "base_endpoint": "https://export.arxiv.org/api/query",
        "host": "export.arxiv.org",
        "content_types": {"application/atom+xml", "application/xml", "text/xml"},
        "source_schema_revision": "ARXIV_ATOM_API",
    },
    "CROSSREF": {
        "endpoint_family": "CROSSREF_WORKS",
        "base_endpoint": "https://api.crossref.org/works",
        "host": "api.crossref.org",
        "content_types": {"application/json"},
        "source_schema_revision": "CROSSREF_REST_WORKS_JSON",
    },
}
ALLOWED_QUERY_KEYS = {
    "ARXIV": {"search_query", "id_list", "start", "max_results", "sortBy", "sortOrder"},
    "CROSSREF": {"query", "query.title", "query.author", "filter", "select", "rows", "cursor", "mailto"},
}
ALLOWED_RESPONSE_HEADERS = {
    "content-type", "content-length", "date", "etag", "last-modified",
    "retry-after", "x-rate-limit-limit", "x-rate-limit-interval",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def with_content_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("content_hash", None)
    result["content_hash"] = canonical_sha256(result)
    return result


def verify_content_hash(value: Mapping[str, Any]) -> None:
    body = {key: item for key, item in value.items() if key != "content_hash"}
    if value.get("content_hash") != canonical_sha256(body):
        raise LiveTransportError("RECEIPT_CONTENT_HASH_MISMATCH", "canonical content_hash mismatch")


class LiveTransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ResponseLike(Protocol):
    status: int
    headers: Mapping[str, Any]

    def read(self, size: int = -1) -> bytes: ...
    def geturl(self) -> str: ...
    def __enter__(self) -> "ResponseLike": ...
    def __exit__(self, *args: Any) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def default_opener(request: urllib.request.Request, timeout_seconds: float) -> ResponseLike:
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout_seconds)


@dataclass(frozen=True)
class TransportPolicy:
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 20.0
    total_timeout_seconds: float = 30.0
    max_response_bytes: int = 2_000_000
    max_header_bytes: int = 32_768
    max_records: int = 20
    user_agent: str = "Memorive-DISCOVERY-ACCEPTANCE/1.0 (metadata-only; contact-policy-frozen)"

    def validate(self) -> None:
        if not 0 < self.connect_timeout_seconds <= self.total_timeout_seconds:
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "connect timeout")
        if not 0 < self.read_timeout_seconds <= self.total_timeout_seconds:
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "read timeout")
        if not 1 <= self.max_response_bytes <= 10_000_000:
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "response size")
        if not 1 <= self.max_header_bytes <= 65_536:
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "header size")
        if not 1 <= self.max_records <= 20:
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "record count")
        if not self.user_agent.strip():
            raise LiveTransportError("TRANSPORT_POLICY_INVALID", "user agent")


def _validate_query(provider: str, query: Mapping[str, Any], policy: TransportPolicy) -> dict[str, str]:
    if provider not in ALLOWED_ENDPOINTS:
        raise LiveTransportError("PROVIDER_NOT_ALLOWED", str(provider))
    if not isinstance(query, Mapping) or not query:
        raise LiveTransportError("QUERY_INVALID", "query object required")
    unknown = sorted(set(str(key) for key in query) - ALLOWED_QUERY_KEYS[provider])
    if unknown:
        raise LiveTransportError("QUERY_KEY_NOT_ALLOWED", ",".join(unknown))
    normalized: dict[str, str] = {}
    for key, value in query.items():
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise LiveTransportError("QUERY_VALUE_INVALID", str(key))
        text = str(value).strip()
        if not text or any(token in text.casefold() for token in ("http://", "https://", "file://")):
            raise LiveTransportError("QUERY_VALUE_INVALID", str(key))
        normalized[str(key)] = text
    count_key = "max_results" if provider == "ARXIV" else "rows"
    try:
        requested = int(normalized.get(count_key, "1"))
    except ValueError as exc:
        raise LiveTransportError("QUERY_RECORD_LIMIT_INVALID", count_key) from exc
    if not 1 <= requested <= policy.max_records:
        raise LiveTransportError("QUERY_RECORD_LIMIT_INVALID", str(requested))
    return dict(sorted(normalized.items()))


def build_url(provider: str, query: Mapping[str, Any], policy: TransportPolicy) -> str:
    normalized = _validate_query(provider, query, policy)
    endpoint = ALLOWED_ENDPOINTS[provider]["base_endpoint"]
    # Quoted arXiv phrases must carry percent-encoded quotes.  Leaving a raw
    # double quote in the request target is rejected by the live endpoint.
    url = endpoint + "?" + urllib.parse.urlencode(normalized, doseq=False, safe=":()[]+-_.*")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_ENDPOINTS[provider]["host"]:
        raise LiveTransportError("ENDPOINT_NOT_ALLOWED", url)
    if parsed.username or parsed.password or parsed.fragment:
        raise LiveTransportError("ENDPOINT_NOT_ALLOWED", url)
    return url


class ReceiptValidator:
    def __init__(self, schema_path: str | Path) -> None:
        self.schema_path = Path(schema_path)
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def validate(self, receipt: Mapping[str, Any], *, pre_send: bool | None = None) -> None:
        errors = sorted(self._validator.iter_errors(receipt), key=lambda item: list(item.path))
        if errors:
            detail = "; ".join(error.message for error in errors[:4])
            raise LiveTransportError("RECEIPT_SCHEMA_INVALID", detail)
        verify_content_hash(receipt)
        if pre_send is True:
            if receipt.get("receipt_stage") != "PRE_SEND" or receipt.get("request_disposition") != "AUTHORIZED_TO_SEND":
                raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "receipt is not authorized pre-send")
            if receipt.get("call_status") != "NOT_STARTED" or receipt.get("external_call_performed") is not False:
                raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "pre-send terminal fields drift")


def _response_headers(headers: Mapping[str, Any], max_header_bytes: int) -> dict[str, str]:
    projected = {
        str(key).casefold(): str(value)
        for key, value in headers.items()
        if str(key).casefold() in ALLOWED_RESPONSE_HEADERS
    }
    if len(canonical_bytes(projected)) > max_header_bytes:
        raise LiveTransportError("RESPONSE_HEADERS_TOO_LARGE", "allowlisted header projection exceeds ceiling")
    return dict(sorted(projected.items()))


def _content_type(headers: Mapping[str, str]) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().casefold()


def _atomic_write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise LiveTransportError("IMMUTABLE_RESPONSE_PATH_EXISTS", str(path))
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".discovery_acceptance_", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise LiveTransportError("IMMUTABLE_RESPONSE_PATH_EXISTS", str(path))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _read_bounded(response: ResponseLike, ceiling: int) -> bytes:
    raw = response.read(ceiling + 1)
    if len(raw) > ceiling:
        raise LiveTransportError("RESPONSE_TOO_LARGE", f">{ceiling}")
    if not raw:
        raise LiveTransportError("EMPTY_RESPONSE", "response body is empty")
    return raw


def _read_error_body_bounded(response: Any, ceiling: int) -> bytes:
    """Read an HTTP error entity without requiring a non-empty body.

    A non-2xx HTTP response is still a physical provider response.  Its exact
    bytes (including the valid zero-byte case) must therefore be hash-bound
    and persisted even though the source parser is intentionally not run.
    """
    raw = b"" if getattr(response, "fp", None) is None else response.read(ceiling + 1)
    if len(raw) > ceiling:
        raise LiveTransportError("RESPONSE_TOO_LARGE", f">{ceiling}")
    return raw


def _terminal_receipt(
    pre_send: Mapping[str, Any], *, status: str, started_at: str, completed_at: str,
    latency_ms: float, response: Mapping[str, Any], parser: Mapping[str, Any],
    error_code: str | None, provider_request_id_hash: str | None,
    retry_disposition: str,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(pre_send))
    value.pop("content_hash", None)
    value["receipt_stage"] = "TERMINAL"
    value["recorded_at"] = completed_at
    value["request_disposition"] = "SENT"
    value["call_status"] = status
    value["external_call_performed"] = True
    value["response"] = copy.deepcopy(dict(response))
    value["parser"] = copy.deepcopy(dict(parser))
    value["provider_identity"]["provider_request_id_hash"] = provider_request_id_hash
    value["retry_disposition"] = retry_disposition
    value["accounting"].update({
        "request_started_at": started_at,
        "response_completed_at": completed_at,
        "latency_ms": round(latency_ms, 3),
    })
    value["side_effect_summary"]["network_requests"] = 1
    value["artifact_bindings"]["output_hash"] = parser.get("output_hash")
    value["artifact_bindings"]["error_hash"] = parser.get("error_hash")
    value["error_code"] = error_code
    eligible = status == "SUCCESS" and parser.get("status") == "SUCCESS"
    value["qualification_eligibility"] = (
        "ELIGIBLE_FOR_Integration_SOURCE_REPLAY" if eligible else "NOT_ELIGIBLE_FOR_Integration_SOURCE_REPLAY"
    )
    value["reason_codes"] = ["SOURCE_RESPONSE_AND_PARSE_BOUND"] if eligible else [error_code or "SOURCE_ATTEMPT_ERROR"]
    return with_content_hash(value)


def build_pre_send_receipt(
    *, recorded_at: str, run_id: str, attempt_id: str, external_call_id: str,
    request_attempt_id: str, retry_of_request_attempt_id: str | None,
    provider: str, operation: str, query: Mapping[str, Any], policy: TransportPolicy,
    profile_identity: Mapping[str, Any], route_identity: Mapping[str, Any],
    artifact_bindings: Mapping[str, Any], authorization: Mapping[str, Any],
    terms_snapshot: Mapping[str, Any], source_evidence_refs: list[Mapping[str, Any]],
    attempt_index: int = 0, retry_ceiling: int = 0,
) -> dict[str, Any]:
    """Build the immutable sidecar that must validate before any socket call."""

    policy.validate()
    endpoint = ALLOWED_ENDPOINTS.get(provider)
    if endpoint is None:
        raise LiveTransportError("PROVIDER_NOT_ALLOWED", provider)
    normalized_query = _validate_query(provider, query, policy)
    request_headers = {
        "Accept": ", ".join(sorted(endpoint["content_types"])),
        "User-Agent": policy.user_agent,
    }
    value = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "record_type": "external_source_request_receipt",
        "binding_mode": "PROSPECTIVE_STRICT",
        "receipt_stage": "PRE_SEND",
        "recorded_at": recorded_at,
        "contract_owner_task_id": "DataContracts",
        "phase_id": "Discovery",
        "task_id": "Integration",
        "module_id": "LITERATURE_DISCOVERY",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "external_call_id": external_call_id,
        "request_attempt_id": request_attempt_id,
        "retry_of_request_attempt_id": retry_of_request_attempt_id,
        "role_family": "NON_MODEL_SCHOLARLY_METADATA_RETRIEVAL",
        "evidence_class": "DISCOVERY_ACCEPTANCE_A1_SOURCE_CANARY",
        "central_5_8_compatibility_status": "APPROVED_BY_CENTRAL_SUCCESSOR",
        "profile_identity": copy.deepcopy(dict(profile_identity)),
        "provider_identity": {
            "connector_id": provider,
            "provider_kind": "NON_MODEL_SCHOLARLY_METADATA_API",
            "requested_model": "N/A_NON_MODEL_API",
            "returned_model": "N/A_NON_MODEL_API",
            "field_applicability": "NOT_APPLICABLE_NON_MODEL_API",
            "returned_identity_status": "NOT_APPLICABLE",
            "provider_request_id_hash": None,
        },
        "request_identity": {
            "operation": operation,
            "endpoint_family": endpoint["endpoint_family"],
            "base_endpoint": endpoint["base_endpoint"],
            "method": "GET",
            "query_hash": canonical_sha256(normalized_query),
            "cursor_hash": None,
            "request_headers_hash": canonical_sha256(dict(sorted(request_headers.items()))),
        },
        "route_identity": copy.deepcopy(dict(route_identity)),
        "artifact_bindings": copy.deepcopy(dict(artifact_bindings)),
        "budget_and_retry": {
            "attempt_index": attempt_index,
            "retry_ceiling": retry_ceiling,
            "global_main_attempt_ceiling": 6,
            "global_retry_ceiling": 2,
            "max_response_bytes": policy.max_response_bytes,
            "max_records": policy.max_records,
            "cost_ceiling_cny": 0,
        },
        "authorization": copy.deepcopy(dict(authorization)),
        "terms_snapshot": copy.deepcopy(dict(terms_snapshot)),
        "request_disposition": "AUTHORIZED_TO_SEND",
        "call_status": "NOT_STARTED",
        "external_call_performed": False,
        "response": {
            "protocol_status": None,
            "content_type": None,
            "raw_response_sha256": None,
            "raw_response_bytes": None,
            "allowed_headers_sha256": None,
            "response_path": None,
            "redirect_chain": [],
            "source_schema_revision": None,
        },
        "parser": {"status": "NOT_RUN", "records_count": None, "output_hash": None, "error_hash": None},
        "retry_disposition": "NOT_APPLICABLE",
        "accounting": {
            "request_started_at": None,
            "response_completed_at": None,
            "latency_ms": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "actual_cost": 0,
            "estimated_cost": 0,
            "currency": "CNY",
            "usage_status": "NOT_APPLICABLE_NON_MODEL_API",
            "pricing_status": "EXACT_ZERO",
        },
        "data_and_rights": {
            "data_ownership": "PUBLIC_METADATA_SUBJECT_TO_CURRENT_TERMS",
            "license_status": "CURRENT_TERMS_REVIEWED",
            "local_materialization": True,
            "fulltext_downloaded": False,
        },
        "side_effect_summary": {
            "network_requests": 0,
            "model_calls": 0,
            "pdf_downloads": 0,
            "production_writes": 0,
            "formal_state_mutations": 0,
        },
        "error_code": None,
        "source_evidence_refs": [copy.deepcopy(dict(item)) for item in source_evidence_refs],
        "qualification_eligibility": "NOT_ELIGIBLE_FOR_Integration_SOURCE_REPLAY",
        "reason_codes": ["PRE_SEND_BINDING_COMPLETE_NOT_YET_SENT"],
    }
    return with_content_hash(value)


def execute_get(
    *, provider: str, query: Mapping[str, Any], pre_send_receipt: Mapping[str, Any],
    response_path: str | Path, receipt_validator: ReceiptValidator,
    parser: Callable[[bytes], Any], policy: TransportPolicy = TransportPolicy(),
    opener: Callable[[urllib.request.Request, float], ResponseLike] = default_opener,
    clock: Callable[[], float] = time.monotonic,
    timestamp: Callable[[], str],
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> tuple[dict[str, Any], Any | None]:
    policy.validate()
    receipt_validator.validate(pre_send_receipt, pre_send=True)
    if expected_run_id is not None and pre_send_receipt.get("run_id") != expected_run_id:
        raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "run_id binding mismatch")
    if expected_attempt_id is not None and pre_send_receipt.get("attempt_id") != expected_attempt_id:
        raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "attempt_id binding mismatch")
    endpoint = ALLOWED_ENDPOINTS.get(provider)
    if endpoint is None:
        raise LiveTransportError("PROVIDER_NOT_ALLOWED", provider)
    url = build_url(provider, query, policy)
    expected = pre_send_receipt["request_identity"]
    request_headers = {"Accept": ", ".join(sorted(endpoint["content_types"])), "User-Agent": policy.user_agent}
    checks = {
        "endpoint_family": endpoint["endpoint_family"],
        "base_endpoint": endpoint["base_endpoint"],
        "method": "GET",
        "query_hash": canonical_sha256(_validate_query(provider, query, policy)),
        "request_headers_hash": canonical_sha256(dict(sorted(request_headers.items()))),
    }
    if pre_send_receipt["provider_identity"]["connector_id"] != provider:
        raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "provider binding mismatch")
    for key, value in checks.items():
        if expected.get(key) != value:
            raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", f"request binding mismatch: {key}")
    if pre_send_receipt["budget_and_retry"]["max_response_bytes"] != policy.max_response_bytes:
        raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "response budget mismatch")
    if pre_send_receipt["budget_and_retry"]["max_records"] != policy.max_records:
        raise LiveTransportError("PRE_SEND_EVIDENCE_BINDING_BLOCKED", "record budget mismatch")

    request = urllib.request.Request(url, headers=request_headers, method="GET")
    started_at = timestamp()
    started = clock()
    raw: bytes | None = None
    header_projection: dict[str, str] = {}
    protocol_status: int | None = None
    content_type: str | None = None
    provider_request_hash: str | None = None
    retryable = False
    target = Path(response_path)
    try:
        with opener(request, policy.total_timeout_seconds) as response:
            protocol_status = int(response.status)
            response_url = response.geturl()
            if urllib.parse.urlsplit(response_url).hostname != endpoint["host"] or response_url.split("?", 1)[0] != endpoint["base_endpoint"]:
                raise LiveTransportError("REDIRECT_NOT_ALLOWED", response_url)
            header_projection = _response_headers(response.headers, policy.max_header_bytes)
            content_type = _content_type(header_projection)
            if content_type not in endpoint["content_types"]:
                raise LiveTransportError("CONTENT_TYPE_NOT_ALLOWED", content_type or "missing")
            if not 200 <= protocol_status <= 299:
                retryable = protocol_status == 429 or 500 <= protocol_status <= 599
                raise LiveTransportError(f"HTTP_{protocol_status}", "non-success status", retryable=retryable)
            raw = _read_bounded(response, policy.max_response_bytes)
            provider_request = header_projection.get("x-request-id") or header_projection.get("x-correlation-id")
            provider_request_hash = sha256_bytes(provider_request.encode("utf-8")) if provider_request else None
        _atomic_write_exclusive(target, raw)
        try:
            parsed = parser(raw)
            parser_output_hash = canonical_sha256(parsed)
            record_count = len(parsed.get("records", [])) if isinstance(parsed, Mapping) else len(parsed)
            if not 0 <= record_count <= policy.max_records:
                raise LiveTransportError("PARSER_RECORD_LIMIT_EXCEEDED", str(record_count))
            parser_fields = {"status": "SUCCESS", "records_count": record_count, "output_hash": parser_output_hash, "error_hash": None}
            status = "SUCCESS"
            error_code = None
        except Exception as exc:
            parsed = None
            error_hash = sha256_bytes(f"{type(exc).__name__}:{exc}".encode("utf-8"))
            parser_fields = {"status": "ERROR", "records_count": None, "output_hash": None, "error_hash": error_hash}
            status = "ERROR"
            error_code = "PARSER_ERROR"
    except urllib.error.HTTPError as exc:
        parsed = None
        protocol_status = int(exc.code)
        try:
            header_projection = _response_headers(exc.headers or {}, policy.max_header_bytes)
            content_type = _content_type(header_projection) or None
        except LiveTransportError:
            header_projection = {}
            content_type = None
        try:
            raw = _read_error_body_bounded(exc, policy.max_response_bytes)
            _atomic_write_exclusive(target, raw)
            error_code = f"HTTP_{protocol_status}"
            retryable = protocol_status == 429 or 500 <= protocol_status <= 599
        except LiveTransportError as body_exc:
            raw = None
            error_code = body_exc.code
            retryable = body_exc.retryable
        error_hash = sha256_bytes(f"{error_code}:{exc.reason}".encode("utf-8"))
        parser_fields = {
            "status": "NOT_RUN",
            "records_count": None,
            "output_hash": None,
            "error_hash": error_hash,
        }
        status = "ERROR"
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        parsed = None
        error_code = "TIMEOUT" if isinstance(exc, (TimeoutError, socket.timeout)) else "TRANSPORT_ERROR"
        error_hash = sha256_bytes(f"{type(exc).__name__}:{exc}".encode("utf-8"))
        parser_fields = {"status": "NOT_RUN", "records_count": None, "output_hash": None, "error_hash": error_hash}
        status = "TIMEOUT" if error_code == "TIMEOUT" else "ERROR"
        retryable = True
    except LiveTransportError as exc:
        parsed = None
        error_code = exc.code
        error_hash = sha256_bytes(f"{exc.code}:{exc.message}".encode("utf-8"))
        parser_fields = {"status": "NOT_RUN", "records_count": None, "output_hash": None, "error_hash": error_hash}
        status = "ERROR"
        retryable = exc.retryable

    completed_at = timestamp()
    latency_ms = max(0.0, (clock() - started) * 1000.0)
    response_fields = {
        "protocol_status": protocol_status,
        "content_type": content_type,
        "raw_response_sha256": sha256_bytes(raw) if raw is not None else None,
        "raw_response_bytes": len(raw) if raw is not None else None,
        "allowed_headers_sha256": canonical_sha256(header_projection) if header_projection else None,
        "response_path": str(target) if raw is not None and target.is_file() else None,
        "redirect_chain": [],
        "source_schema_revision": (
            endpoint["source_schema_revision"]
            if raw is not None and protocol_status is not None and 200 <= protocol_status <= 299
            else None
        ),
    }
    attempt_index = int(pre_send_receipt["budget_and_retry"]["attempt_index"])
    retry_ceiling = int(pre_send_receipt["budget_and_retry"]["retry_ceiling"])
    retry_disposition = (
        "RETRY_ALLOWED_TRANSIENT" if retryable and attempt_index < retry_ceiling else
        "RETRY_DENIED" if error_code is not None else "NOT_APPLICABLE"
    )
    terminal = _terminal_receipt(
        pre_send_receipt, status=status, started_at=started_at, completed_at=completed_at,
        latency_ms=latency_ms, response=response_fields, parser=parser_fields,
        error_code=error_code, provider_request_id_hash=provider_request_hash,
        retry_disposition=retry_disposition,
    )
    receipt_validator.validate(terminal)
    return terminal, parsed


__all__ = [
    "ALLOWED_ENDPOINTS", "LiveTransportError", "ReceiptValidator", "TransportPolicy",
    "build_pre_send_receipt", "build_url", "canonical_sha256", "execute_get",
    "sha256_bytes", "with_content_hash",
]
