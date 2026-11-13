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


RUN_ID = "SOURCE_CONNECTORS_LITERATURE_DISCOVERY_20260810_190016_candidate002"
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


def normalize_source_date(value: str | None, connector_id: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) < 10:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    token = value[:10]
    try:
        date.fromisoformat(token)
    except ValueError as exc:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL") from exc
    return token


def offline_fixture_root() -> Path:
    module_root = Path(__file__).resolve().parent
    candidates = (
        module_root / "tests" / "fixtures" / "source_connectors",
        module_root.parent / "fixtures",
    )
    existing = {path.resolve() for path in candidates if path.is_dir()}
    if len(existing) != 1:
        raise ConnectorError("FORBIDDEN_SIDE_EFFECT", "LITERATURE_DISCOVERY", False, "NONE")
    return next(iter(existing))


def resolve_offline_fixture_path(value: str | Path, connector_id: str) -> Path:
    root = offline_fixture_root()
    try:
        path = Path(value).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ConnectorError("FORBIDDEN_SIDE_EFFECT", connector_id, False, "NONE") from exc
    if not path.is_file():
        raise ConnectorError("FORBIDDEN_SIDE_EFFECT", connector_id, False, "NONE")
    return path


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

    def as_record(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "connector_id": self.connector_id,
            "retryable": self.retryable,
            "coverage_impact": self.coverage_impact,
        }


@dataclass(frozen=True)
class SearchResult:
    page: dict[str, Any]
    snapshot: dict[str, Any]
    normalized_payload: dict[str, Any]


class DeterministicCache:
    def __init__(self) -> None:
        self._values: dict[str, SearchResult] = {}

    def get(self, key: str) -> SearchResult | None:
        value = self._values.get(key)
        return copy.deepcopy(value) if value is not None else None

    def put(self, key: str, value: SearchResult) -> None:
        if key in self._values:
            raise ConnectorError("DUPLICATE_CACHE_KEY_WRITE", "LITERATURE_DISCOVERY", False, "NONE")
        self._values[key] = copy.deepcopy(value)


def _cursor_payload(
    connector_id: str,
    query_hash: str,
    start_index: int,
    source_cursor: str | None,
) -> dict[str, Any]:
    return {
        "connector_id": connector_id,
        "query_hash": query_hash,
        "start_index": start_index,
        "source_cursor": source_cursor,
    }


def encode_cursor(
    connector_id: str,
    query_hash: str,
    start_index: int,
    source_cursor: str | None = None,
) -> str:
    payload = canonical_json(_cursor_payload(connector_id, query_hash, start_index, source_cursor))
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    checksum = sha256_bytes(payload)[:16]
    return f"cur_{encoded}.{checksum}"


def decode_cursor(cursor: str, connector_id: str, query_hash: str) -> dict[str, Any]:
    match = re.fullmatch(r"cur_([A-Za-z0-9_-]+)\.([a-f0-9]{16})", cursor)
    if not match:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL")
    encoded, checksum = match.groups()
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(encoded + padding)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL") from exc
    if sha256_bytes(payload_bytes)[:16] != checksum:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL")
    expected_keys = {"connector_id", "query_hash", "start_index", "source_cursor"}
    if set(payload) != expected_keys:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL")
    if payload["connector_id"] != connector_id or payload["query_hash"] != query_hash:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL")
    if not isinstance(payload["start_index"], int) or payload["start_index"] < 0:
        raise ConnectorError("INVALID_CURSOR", connector_id, False, "TOTAL")
    return payload


def _snapshot_id(connector_id: str, raw_hash: str) -> str:
    return "snapshot_" + sha256_json([connector_id, CONNECTOR_REVISION, raw_hash])[:24]


def make_snapshot(
    *,
    connector_id: str,
    endpoint_family: str,
    source_schema_revision: str,
    fixture_path: Path,
    raw_bytes: bytes,
    normalized_payload: Mapping[str, Any],
) -> dict[str, Any]:
    raw_hash = sha256_bytes(raw_bytes)
    return {
        "schema_version": "discovery.data_contracts.source-snapshot.0.1",
        "snapshot_id": _snapshot_id(connector_id, raw_hash),
        "connector_id": connector_id,
        "connector_revision": CONNECTOR_REVISION,
        "endpoint_family": endpoint_family,
        "source_schema_revision": source_schema_revision,
        "captured_at": FIXED_OBSERVED_AT,
        "request_id": None,
        "fixture_classification": "SYNTHETIC_PUBLIC_SAFE_A0",
        "raw_media_type": "application/atom+xml" if connector_id == "ARXIV" else "application/json",
        "raw_bytes_sha256": raw_hash,
        "normalized_payload_sha256": sha256_json(normalized_payload),
        "byte_length": len(raw_bytes),
        "payload_locator": f"fixtures/{fixture_path.name}",
        "rights_status": "SYNTHETIC_SELF_OWNED",
        "materialized_fulltext": False,
        "network_used": False,
    }


def make_frozen_response_snapshot(
    *,
    connector_id: str,
    endpoint_family: str,
    source_schema_revision: str,
    raw_bytes: bytes,
    normalized_payload: Mapping[str, Any],
    request_id: str,
    response_locator: str,
    captured_at: str,
) -> dict[str, Any]:
    """Project one already-acquired A1 response into the offline replay contract.

    This function performs no I/O.  ``network_used`` describes the current replay
    operation (which is offline); the acquisition request remains bound by
    ``request_id`` and the separately frozen terminal request receipt.
    """

    if connector_id not in {"ARXIV", "CROSSREF"}:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    if not isinstance(request_id, str) or not request_id:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    if not isinstance(response_locator, str) or not response_locator:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    if not isinstance(captured_at, str) or not captured_at:
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    raw_hash = sha256_bytes(raw_bytes)
    return {
        "schema_version": "discovery.data_contracts.source-snapshot.0.2",
        "snapshot_id": _snapshot_id(connector_id, raw_hash),
        "connector_id": connector_id,
        "connector_revision": CONNECTOR_REVISION,
        "endpoint_family": endpoint_family,
        "source_schema_revision": source_schema_revision,
        "captured_at": captured_at,
        "request_id": request_id,
        "fixture_classification": "FROZEN_PUBLIC_METADATA_A1",
        "raw_media_type": "application/atom+xml" if connector_id == "ARXIV" else "application/json",
        "raw_bytes_sha256": raw_hash,
        "normalized_payload_sha256": sha256_json(normalized_payload),
        "byte_length": len(raw_bytes),
        "payload_locator": response_locator,
        "rights_status": "PUBLIC_METADATA_CURRENT_TERMS_REVIEWED",
        "materialized_fulltext": False,
        "network_used": False,
    }


def verify_snapshot(
    snapshot: Mapping[str, Any],
    raw_bytes: bytes,
    normalized_payload: Mapping[str, Any],
) -> None:
    if snapshot.get("raw_bytes_sha256") != sha256_bytes(raw_bytes):
        raise ConnectorError("SNAPSHOT_HASH_MISMATCH", str(snapshot.get("connector_id")), False, "TOTAL")
    if snapshot.get("normalized_payload_sha256") != sha256_json(normalized_payload):
        raise ConnectorError("SNAPSHOT_HASH_MISMATCH", str(snapshot.get("connector_id")), False, "TOTAL")
    if snapshot.get("byte_length") != len(raw_bytes):
        raise ConnectorError("SNAPSHOT_HASH_MISMATCH", str(snapshot.get("connector_id")), False, "TOTAL")


def _observation_object_id(connector_id: str, source_record_id: str, snapshot_id: str) -> str:
    return "source_observation_" + sha256_json([connector_id, source_record_id, snapshot_id])[:24]


def _finalize_content_hash(observation: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(observation)
    result.pop("content_hash", None)
    result["content_hash"] = sha256_json(result)
    return result


def make_observation(
    connector_id: str,
    snapshot: Mapping[str, Any],
    record: Mapping[str, Any],
) -> dict[str, Any]:
    source_record_id = str(record["source_record_id"])
    observation: dict[str, Any] = {
        "schema_version": "discovery.data_contracts.source-observation.0.1",
        "object_type": "SourceObservation",
        "object_id": _observation_object_id(connector_id, source_record_id, str(snapshot["snapshot_id"])),
        "revision": 1,
        "producer": "SOURCE-CONNECTORS/LITERATURE_DISCOVERY",
        "single_writer": "SOURCE-CONNECTORS/LITERATURE_DISCOVERY",
        "consumers": ["BIBLIOGRAPHIC-IDENTITY/LITERATURE_DISCOVERY"],
        "run_id": RUN_ID,
        "source_connector": connector_id,
        "source_snapshot_id": snapshot["snapshot_id"],
        "discovery_origin": "SYNTHETIC_A0_REPLAY",
        "observed_at": FIXED_OBSERVED_AT,
        "identifiers": copy.deepcopy(record["identifiers"]),
        "bibliographic": copy.deepcopy(record["bibliographic"]),
        "location_claims": copy.deepcopy(record["location_claims"]),
        "identity_status": "VERIFIED",
        "access_status": "METADATA_ONLY",
        "decision_status": "PENDING",
        "candidate_card_status": "NOT_REQUESTED",
        "promotion_status": "NOT_ELIGIBLE",
        "source_evidence": {
            "snapshot_sha256": snapshot["raw_bytes_sha256"],
            "normalized_record_sha256": sha256_json(record),
            "fixture_classification": "SYNTHETIC_PUBLIC_SAFE_A0",
            "network_used": False,
        },
        "partial_fields": copy.deepcopy(record.get("partial_fields", [])),
        "errors": [],
    }
    return _finalize_content_hash(observation)


def make_frozen_replay_observation(
    connector_id: str,
    snapshot: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Create the ServiceContracts input for an offline replay of a frozen real response."""

    if snapshot.get("schema_version") != "discovery.data_contracts.source-snapshot.0.2":
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    if snapshot.get("fixture_classification") != "FROZEN_PUBLIC_METADATA_A1":
        raise ConnectorError("SOURCE_SCHEMA_DRIFT", connector_id, False, "TOTAL")
    source_record_id = str(record["source_record_id"])
    observation: dict[str, Any] = {
        "schema_version": "discovery.data_contracts.source-observation.0.2",
        "object_type": "SourceObservation",
        "object_id": _observation_object_id(connector_id, source_record_id, str(snapshot["snapshot_id"])),
        "revision": 1,
        "producer": "SOURCE-CONNECTORS/LITERATURE_DISCOVERY",
        "single_writer": "SOURCE-CONNECTORS/LITERATURE_DISCOVERY",
        "consumers": ["BIBLIOGRAPHIC-IDENTITY/LITERATURE_DISCOVERY"],
        "run_id": run_id,
        "source_connector": connector_id,
        "source_snapshot_id": snapshot["snapshot_id"],
        "discovery_origin": "KEYWORD_SEARCH",
        "observed_at": snapshot["captured_at"],
        "identifiers": copy.deepcopy(record["identifiers"]),
        "bibliographic": copy.deepcopy(record["bibliographic"]),
        "location_claims": copy.deepcopy(record["location_claims"]),
        "identity_status": "VERIFIED",
        "access_status": "METADATA_ONLY",
        "decision_status": "PENDING",
        "candidate_card_status": "NOT_REQUESTED",
        "promotion_status": "NOT_ELIGIBLE",
        "source_evidence": {
            "snapshot_sha256": snapshot["raw_bytes_sha256"],
            "normalized_record_sha256": sha256_json(record),
            "fixture_classification": "FROZEN_PUBLIC_METADATA_A1",
            "network_used": False,
            "acquisition_request_id": snapshot["request_id"],
        },
        "partial_fields": copy.deepcopy(record.get("partial_fields", [])),
        "errors": [],
    }
    return _finalize_content_hash(observation)


class BaseOfflineConnector:
    connector_id: str
    endpoint_family: str
    source_schema_revision: str

    def __init__(self, capability: Mapping[str, Any]) -> None:
        self.capability = copy.deepcopy(dict(capability))
        self.cache = DeterministicCache()

    def _parse(self, raw_bytes: bytes) -> dict[str, Any]:
        raise NotImplementedError

    def search(
        self,
        discovery_query: Mapping[str, Any],
        cursor: str | None,
        run_context: Mapping[str, Any],
        source_snapshot: str | Path,
    ) -> SearchResult:
        if run_context.get("run_id") != RUN_ID or run_context.get("network") != "DISABLED_BY_CONTRACT":
            raise ConnectorError("NETWORK_DISABLED_BY_CONTRACT", self.connector_id, False, "NONE")
        query_hash = sha256_json(discovery_query)
        cursor_payload = None if cursor is None else decode_cursor(cursor, self.connector_id, query_hash)
        fixture_path = resolve_offline_fixture_path(source_snapshot, self.connector_id)
        raw_bytes = fixture_path.read_bytes()
        normalized_payload = self._parse(raw_bytes)
        expected_start = 0 if cursor_payload is None else cursor_payload["start_index"]
        if normalized_payload["start_index"] != expected_start:
            raise ConnectorError("INVALID_CURSOR", self.connector_id, False, "TOTAL")
        if (
            not normalized_payload["records"]
            and expected_start < normalized_payload["total_results"]
        ):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        snapshot = make_snapshot(
            connector_id=self.connector_id,
            endpoint_family=self.endpoint_family,
            source_schema_revision=self.source_schema_revision,
            fixture_path=fixture_path,
            raw_bytes=raw_bytes,
            normalized_payload=normalized_payload,
        )
        verify_snapshot(snapshot, raw_bytes, normalized_payload)
        cursor_hash = None if cursor is None else sha256_bytes(cursor.encode("utf-8"))
        cache_key = sha256_json(
            [
                self.connector_id,
                CONNECTOR_REVISION,
                query_hash,
                cursor_hash,
                snapshot["raw_bytes_sha256"],
            ]
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            cached.page["cache_status"] = "HIT"
            return cached
        observations = [make_observation(self.connector_id, snapshot, record) for record in normalized_payload["records"]]
        consumed = normalized_payload["start_index"] + len(normalized_payload["records"])
        next_cursor = None
        if consumed < normalized_payload["total_results"]:
            next_cursor = encode_cursor(
                self.connector_id,
                query_hash,
                consumed,
                normalized_payload.get("next_source_cursor"),
            )
        page = {
            "schema_version": "discovery.data_contracts.source-observation-page.0.1",
            "connector_id": self.connector_id,
            "connector_revision": CONNECTOR_REVISION,
            "operation": "search",
            "run_id": RUN_ID,
            "query_hash": query_hash,
            "cursor": cursor,
            "cursor_hash": cursor_hash,
            "observations": observations,
            "next_cursor": next_cursor,
            "snapshot_id": snapshot["snapshot_id"],
            "cache_status": "MISS",
            "coverage_status": "COMPLETE",
            "errors": [],
        }
        result = SearchResult(page=page, snapshot=snapshot, normalized_payload=normalized_payload)
        self.cache.put(cache_key, result)
        return result

    def resolve(
        self,
        identifier_or_bibliographic_hint: str,
        run_context: Mapping[str, Any],
        source_snapshots: Iterable[str | Path],
    ) -> dict[str, Any]:
        query = {"mode": "RESOLVE", "hint": identifier_or_bibliographic_hint.strip().lower()}
        cursor: str | None = None
        matches: list[dict[str, Any]] = []
        for fixture in source_snapshots:
            result = self.search(query, cursor, run_context, fixture)
            for observation in result.page["observations"]:
                values = [value for value in observation["identifiers"].values() if isinstance(value, str)]
                if query["hint"] in {value.lower() for value in values}:
                    matches.append(observation)
            cursor = result.page["next_cursor"]
        return {
            "connector_id": self.connector_id,
            "status": "VERIFIED" if matches else "UNRESOLVED",
            "observations": matches,
            "network_used": False,
        }

    def get_open_locations(
        self,
        work_identity: str,
        run_context: Mapping[str, Any],
        source_snapshots: Iterable[str | Path],
    ) -> dict[str, Any]:
        resolution = self.resolve(work_identity, run_context, source_snapshots)
        locations = [
            location
            for observation in resolution["observations"]
            for location in observation["location_claims"]
        ]
        return {
            "connector_id": self.connector_id,
            "status": "LOCATION_CLAIMS_PRESENT" if locations else "NO_LOCATION_CLAIM",
            "locations": locations,
            "materialized_fulltext": False,
            "network_used": False,
        }

    def healthcheck(self, run_context: Mapping[str, Any]) -> dict[str, Any]:
        if run_context.get("network") != "DISABLED_BY_CONTRACT":
            raise ConnectorError("NETWORK_DISABLED_BY_CONTRACT", self.connector_id, False, "NONE")
        return {
            "connector_id": self.connector_id,
            "connector_revision": CONNECTOR_REVISION,
            "capability_status": self.capability["implementation_status"],
            "network_check": "NOT_RUN_BY_CONTRACT",
            "credential_values_read": False,
            "external_calls": 0,
        }


class ArxivOfflineConnector(BaseOfflineConnector):
    connector_id = "ARXIV"
    endpoint_family = "ARXIV_QUERY_A0_FIXTURE"
    source_schema_revision = "ATOM_LIKE_SYNTHETIC_0.1"

    _atom = "{http://www.w3.org/2005/Atom}"
    _open = "{http://a9.com/-/spec/opensearch/1.1/}"
    _arxiv = "{http://arxiv.org/schemas/atom}"

    def _parse(self, raw_bytes: bytes) -> dict[str, Any]:
        try:
            root = ET.fromstring(raw_bytes)
        except ET.ParseError as exc:
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL") from exc
        if root.tag != self._atom + "feed":
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        try:
            total = int(root.findtext(self._open + "totalResults", default=""))
            start = int(root.findtext(self._open + "startIndex", default=""))
            items_per_page = int(root.findtext(self._open + "itemsPerPage", default=""))
        except ValueError as exc:
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL") from exc
        records: list[dict[str, Any]] = []
        for entry in root.findall(self._atom + "entry"):
            raw_id = entry.findtext(self._atom + "id")
            title = normalized_text(entry.findtext(self._atom + "title"))
            authors = [normalized_text(node.findtext(self._atom + "name")) for node in entry.findall(self._atom + "author")]
            authors = [name for name in authors if name]
            if not raw_id or not title or not authors:
                raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
            arxiv_id, source_record_id = normalize_arxiv_id(raw_id)
            doi = normalize_doi(entry.findtext(self._arxiv + "doi"))
            locations: list[dict[str, Any]] = []
            for link in entry.findall(self._atom + "link"):
                locator = link.attrib.get("href")
                media_type = link.attrib.get("type")
                if not locator:
                    continue
                locations.append(
                    {
                        "kind": "PDF_LOCATION_CLAIM" if media_type == "application/pdf" else "LANDING_PAGE",
                        "locator": locator,
                        "materialized": False,
                        "rights_status": "LOCATION_CLAIM_ONLY",
                    }
                )
            records.append(
                {
                    "source_record_id": source_record_id,
                    "identifiers": {
                        "arxiv_id": arxiv_id,
                        "doi": doi,
                        "pmid": None,
                        "source_record_id": source_record_id,
                    },
                    "bibliographic": {
                        "title": title,
                        "authors": [{"display_name": name, "given": None, "family": None} for name in authors],
                        "published_date": normalize_source_date(
                            entry.findtext(self._atom + "published"), self.connector_id
                        ),
                        "updated_date": normalize_source_date(
                            entry.findtext(self._atom + "updated"), self.connector_id
                        ),
                        "abstract": normalized_text(entry.findtext(self._atom + "summary")),
                        "record_type": "arxiv-preprint",
                    },
                    "location_claims": locations,
                    "partial_fields": [] if doi else ["identifiers.doi"],
                }
            )
        if len(records) > items_per_page or start < 0 or total < start + len(records):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        return {
            "source_schema_revision": self.source_schema_revision,
            "total_results": total,
            "start_index": start,
            "items_per_page": items_per_page,
            "next_source_cursor": None,
            "records": records,
        }


class CrossrefOfflineConnector(BaseOfflineConnector):
    connector_id = "CROSSREF"
    endpoint_family = "CROSSREF_WORKS_A0_FIXTURE"
    source_schema_revision = "CROSSREF_LIKE_SYNTHETIC_0.1"

    @staticmethod
    def _date_from_parts(value: Any) -> str | None:
        if value is None:
            return None
        try:
            parts = value["date-parts"][0]
        except (KeyError, IndexError, TypeError):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", "CROSSREF", False, "TOTAL")
        if not isinstance(parts, list) or not parts:
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", "CROSSREF", False, "TOTAL")
        if any(isinstance(item, bool) for item in parts[:3]):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", "CROSSREF", False, "TOTAL")
        try:
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            day = int(parts[2]) if len(parts) > 2 else 1
            return date(year, month, day).isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", "CROSSREF", False, "TOTAL") from exc

    def _parse(self, raw_bytes: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        if not isinstance(message.get("total-results"), int) or not isinstance(message.get("items-per-page"), int):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        raw_items = message["items"]
        records: list[dict[str, Any]] = []
        skipped_records: list[dict[str, Any]] = []
        for item_index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
            raw_doi = item.get("DOI")
            doi = normalize_doi(raw_doi) if isinstance(raw_doi, str) else None
            titles = item.get("title")
            authors_value = item.get("author")
            title = (
                normalized_text(titles[0])
                if isinstance(titles, list) and titles and isinstance(titles[0], str)
                else None
            )
            authors: list[dict[str, Any]] = []
            invalid_author_entries = 0
            if isinstance(authors_value, list):
                for author in authors_value:
                    if not isinstance(author, dict):
                        invalid_author_entries += 1
                        continue
                    raw_given = author.get("given")
                    raw_family = author.get("family")
                    given = normalized_text(raw_given) if isinstance(raw_given, str) else None
                    family = normalized_text(raw_family) if isinstance(raw_family, str) else None
                    display = normalized_text(" ".join(value for value in (given, family) if value))
                    if not display:
                        invalid_author_entries += 1
                        continue
                    authors.append({"display_name": display, "given": given, "family": family})
            missing_reasons = []
            if not doi:
                missing_reasons.append("CORE_IDENTIFIER_MISSING")
            if not title:
                missing_reasons.append("CORE_TITLE_MISSING")
            if not authors:
                missing_reasons.append("CORE_AUTHORS_MISSING")
            if missing_reasons:
                skipped_records.append(
                    {
                        "item_index": item_index,
                        "source_record_id": raw_doi if isinstance(raw_doi, str) else None,
                        "reason_codes": missing_reasons,
                    }
                )
                continue
            locator = item.get("URL")
            locations = []
            if isinstance(locator, str) and locator:
                locations.append(
                    {
                        "kind": "METADATA_RECORD",
                        "locator": locator,
                        "materialized": False,
                        "rights_status": "LOCATION_CLAIM_ONLY",
                    }
                )
            published_date = self._date_from_parts(item.get("published"))
            partial_fields = ["bibliographic.updated_date"]
            if published_date is None:
                partial_fields.append("bibliographic.published_date")
            if invalid_author_entries:
                partial_fields.append("bibliographic.authors.partial")
            records.append(
                {
                    "source_record_id": doi,
                    "identifiers": {
                        "arxiv_id": None,
                        "doi": doi,
                        "pmid": None,
                        "source_record_id": doi,
                    },
                    "bibliographic": {
                        "title": title,
                        "authors": authors,
                        "published_date": published_date,
                        "updated_date": None,
                        "abstract": normalized_text(item.get("abstract")),
                        "record_type": str(item.get("type") or "unknown"),
                    },
                    "location_claims": locations,
                    "partial_fields": partial_fields,
                }
            )
        items_per_page = message["items-per-page"]
        total = message["total-results"]
        if (
            isinstance(items_per_page, bool)
            or isinstance(total, bool)
            or items_per_page < 0
            or total < 0
            or len(raw_items) > items_per_page
        ):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        query = message.get("query")
        if isinstance(query, dict) and "start-index" in query:
            start = query["start-index"]
            if isinstance(start, bool) or not isinstance(start, int) or start < 0:
                raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        elif message.get("next-cursor") is not None:
            # The historical synthetic first-page fixture exposes only a next cursor.
            start = 0
        else:
            # The historical synthetic terminal-page fixture has no query metadata.
            # Use the raw page size so excluded partial records do not shift the offset.
            start = max(0, total - len(raw_items))
        if total < start + len(raw_items):
            raise ConnectorError("SOURCE_SCHEMA_DRIFT", self.connector_id, False, "TOTAL")
        return {
            "source_schema_revision": self.source_schema_revision,
            "total_results": total,
            "start_index": start,
            "items_per_page": items_per_page,
            "next_source_cursor": message.get("next-cursor"),
            "records": records,
            "skipped_records": skipped_records,
        }
