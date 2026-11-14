"""SOURCE-CONNECTORS/LITERATURE_DISCOVERY pure-offline connector candidate.

This module deliberately contains no HTTP, socket, DNS, browser, subprocess, or
provider SDK transport.  It accepts exact fixture bytes, emits evidence-bound
objects, and fails closed before any external request can be sent.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


FIXED_OBSERVED_AT = "2026-08-10T00:00:00Z"
CONNECTOR_REVISION = "r0.1"


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    result = " ".join(value.split())
    return result or None


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", "UNKNOWN", False, "TOTAL")
    return doi


def normalize_arxiv_id(value: str) -> tuple[str, str]:
    token = value.rstrip("/").rsplit("/", 1)[-1].lower()
    match = re.fullmatch(r"(\d{4}\.\d{4,5})(v\d+)?", token)
    if not match:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", "ARXIV", False, "TOTAL")
    return match.group(1), token


class ConnectorError(RuntimeError):
    def __init__(
        self,
        code: str,
        connector_id: str,
        retryable: bool,
        coverage_impact: str,
    ) -> None:
        super().__init__(f"{connector_id}:{code}")
        self.code = code
        self.connector_id = connector_id
        self.retryable = retryable
        self.coverage_impact = coverage_impact

@dataclass(frozen=True)
class SearchResult:
    page: dict[str, Any]
    snapshot: dict[str, Any]
    normalized_payload: dict[str, Any]


def _finalize_content_hash(observation: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(observation)
    result.pop("content_hash", None)
    result["content_hash"] = sha256_json(result)
    return result
