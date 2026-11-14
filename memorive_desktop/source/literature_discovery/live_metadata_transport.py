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


def _read_bounded(response: ResponseLike, ceiling: int) -> bytes:
    raw = response.read(ceiling + 1)
    if len(raw) > ceiling:
        raise LiveTransportError("RESPONSE_TOO_LARGE", f">{ceiling}")
    if not raw:
        raise LiveTransportError("EMPTY_RESPONSE", "response body is empty")
    return raw


__all__ = [
    "ALLOWED_ENDPOINTS",
    "LiveTransportError",
    "TransportPolicy",
    "canonical_sha256",
    "sha256_bytes",
]
