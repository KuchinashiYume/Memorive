"""Frozen source-snapshot loading, exact integrity checks and central dedup."""

from __future__ import annotations

import json
import math
import re
import stat
from pathlib import Path
from typing import Any

from .adapters import adapt_record
from .canonical import parse_datetime, require_safe_text, sha256_bytes, sha256_file
from .errors import ManifestIntegrityError, SourceRecordError
from .issues import make_issue

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - frozen environment prerequisite
    raise RuntimeError("jsonschema is required by the frozen source-manifest contract") from exc

REQUIRED_RUNTIME_POLICY = {
    "provider": "NONE",
    "external_calls": 0,
    "external_tokens": 0,
    "cost_cny": 0,
    "change_digest_primary": "DISABLED",
    "report_timezone": "Asia/Shanghai",
}
MANIFEST_FIELDS = {
    "manifest_version",
    "snapshot_id",
    "snapshot_observed_at",
    "data_classification",
    "required_source_systems",
    "runtime_policy",
    "files",
    "purpose",
}
MEMBER_FIELDS = {
    "path",
    "source_system",
    "required",
    "sha256",
    "bytes",
    "record_count",
    "line_ending",
    "record_hash_scope",
    "record_sha256",
}
RECORD_HASH_SCOPE = "UTF8_JSONL_LINE_BYTES_EXCLUDING_TERMINATING_LF"
SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
SOURCE_MANIFEST_SCHEMA_SHA256 = "49311FEBE91AA609E0C72693A67C952855F4AD89E0652BA4F012140C4C1208B0"
SOURCE_MANIFEST_SCHEMA_BYTES = 3285


def _safe_text(value: Any, field: str) -> str:
    try:
        return require_safe_text(value, field)
    except (TypeError, ValueError) as exc:
        raise ManifestIntegrityError(str(exc)) from exc


def _exact_type_equal(observed: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(observed, dict)
            and set(observed) == set(expected)
            and all(_exact_type_equal(observed[key], value) for key, value in expected.items())
        )
    return type(observed) is type(expected) and observed == expected


def _safe_member(base: Path, relative: str) -> Path:
    if (
        "\\" in relative
        or any(char in relative for char in (":", "#", "?"))
        or relative.startswith(("/", "//"))
        or re.match(r"^[A-Za-z]:", relative)
    ):
        raise ManifestIntegrityError(f"source member path is not a canonical relative path: {relative}")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ManifestIntegrityError(f"source member path contains an unsafe segment: {relative}")
    if not re.fullmatch(r"source_snapshot/[A-Za-z0-9._/-]+\.jsonl", relative):
        raise ManifestIntegrityError(f"source member is outside the frozen source_snapshot JSONL subtree: {relative}")
    lexical = base / relative
    current = base
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for part in relative.split("/"):
        if current.exists():
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
            if current.is_symlink() or bool(attributes & reparse_flag):
                raise ManifestIntegrityError(f"source member chain contains a reparse point: {relative}")
        current = current / part
    if current.exists():
        attributes = getattr(current.lstat(), "st_file_attributes", 0)
        if current.is_symlink() or bool(attributes & reparse_flag):
            raise ManifestIntegrityError(f"source member is a reparse point: {relative}")
    candidate = lexical.resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ManifestIntegrityError(f"source member escapes manifest root: {relative}") from exc
    return candidate


def _parse_json_object(data: bytes, label: str) -> dict[str, Any]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ManifestIntegrityError(f"{label} must not contain a UTF-8 BOM")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-I-JSON number {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    try:
        def parse_int(text: str) -> int:
            value = int(text)
            if abs(value) > 9_007_199_254_740_991:
                raise ValueError("integer outside the I-JSON exact IEEE-754 range")
            return value

        def parse_float(text: str) -> float:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError("non-finite I-JSON number")
            return value

        value = json.loads(
            data.decode("utf-8"),
            parse_constant=reject_constant,
            parse_int=parse_int,
            parse_float=parse_float,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManifestIntegrityError(f"{label} is not strict UTF-8 I-JSON") from exc
    if not isinstance(value, dict):
        raise ManifestIntegrityError(f"{label} root must be an object")
    return value


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if set(manifest) != MANIFEST_FIELDS:
        raise ManifestIntegrityError(
            f"source manifest fields mismatch: missing={sorted(MANIFEST_FIELDS - set(manifest))}, "
            f"extra={sorted(set(manifest) - MANIFEST_FIELDS)}"
        )
    if manifest["manifest_version"] != "2.0":
        raise ManifestIntegrityError("unsupported source manifest version")
    _safe_text(manifest["snapshot_id"], "snapshot_id")
    _safe_text(manifest["purpose"], "purpose")
    if manifest["data_classification"] != "PUBLIC_SAFE_SYNTHETIC":
        raise ManifestIntegrityError("only PUBLIC_SAFE_SYNTHETIC source snapshots are admitted")
    if not _exact_type_equal(manifest["runtime_policy"], REQUIRED_RUNTIME_POLICY):
        raise ManifestIntegrityError("runtime policy differs in value or JSON type")
    systems = manifest["required_source_systems"]
    if (
        not isinstance(systems, list)
        or any(not isinstance(value, str) for value in systems)
        or len(systems) != len(set(systems))
        or systems != ["M8", "M11", "M13"]
    ):
        raise ManifestIntegrityError("M8, M11 and M13 must be the exact required-source set")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise ManifestIntegrityError("manifest files must be a non-empty list")
    try:
        parse_datetime(manifest["snapshot_observed_at"], field_name="snapshot_observed_at")
    except (TypeError, ValueError) as exc:
        raise ManifestIntegrityError(str(exc)) from exc


def _validate_member(member: Any, index: int) -> dict[str, Any]:
    if not isinstance(member, dict) or set(member) != MEMBER_FIELDS:
        raise ManifestIntegrityError(f"manifest member {index} fields mismatch")
    relative = _safe_text(member["path"], f"files[{index}].path")
    source_system = _safe_text(member["source_system"], f"files[{index}].source_system")
    if source_system not in {"M8", "M11", "M13"}:
        raise ManifestIntegrityError(f"unsupported source_system: {source_system}")
    if type(member["required"]) is not bool:
        raise ManifestIntegrityError("member.required must be a JSON boolean")
    if isinstance(member["bytes"], bool) or not isinstance(member["bytes"], int) or member["bytes"] < 1:
        raise ManifestIntegrityError("member.bytes must be a positive JSON integer")
    if (
        isinstance(member["record_count"], bool)
        or not isinstance(member["record_count"], int)
        or member["record_count"] < 0
    ):
        raise ManifestIntegrityError("member.record_count must be a non-negative JSON integer")
    sha = _safe_text(member["sha256"], f"files[{index}].sha256")
    if not SHA256_RE.fullmatch(sha):
        raise ManifestIntegrityError("member.sha256 must be uppercase SHA-256")
    if member["line_ending"] != "LF" or member["record_hash_scope"] != RECORD_HASH_SCOPE:
        raise ManifestIntegrityError("JSONL line/hash scope differs from the frozen contract")
    record_hashes = member["record_sha256"]
    if (
        not isinstance(record_hashes, list)
        or len(record_hashes) != member["record_count"]
        or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in record_hashes)
    ):
        raise ManifestIntegrityError("record_sha256 must be an ordered uppercase SHA-256 list")
    return {
        "path": relative,
        "source_system": source_system,
        "required": member["required"],
        "sha256": sha,
        "bytes": member["bytes"],
        "record_count": member["record_count"],
        "line_ending": "LF",
        "record_hash_scope": RECORD_HASH_SCOPE,
        "record_sha256": list(record_hashes),
    }


def _jsonl_records(raw: bytes, member: dict[str, Any]) -> list[bytes]:
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise ManifestIntegrityError(f"source member must be BOM-free LF JSONL with final LF: {member['path']}")
    records = raw[:-1].split(b"\n")
    if any(not line for line in records):
        raise ManifestIntegrityError(f"source member contains an empty JSONL record: {member['path']}")
    if len(records) != member["record_count"]:
        raise ManifestIntegrityError(f"source member record_count mismatch: {member['path']}")
    observed = [sha256_bytes(line) for line in records]
    if observed != member["record_sha256"]:
        raise ManifestIntegrityError(f"source member record hash mismatch: {member['path']}")
    return records


def _deduplicate(events: list[dict[str, Any]], observed_at: str) -> tuple[list[dict], list[dict], list[dict], int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(event["event_id"], []).append(event)
    admitted: list[dict] = []
    conflicts: list[dict] = []
    issues: list[dict] = []
    duplicate_count = 0
    for event_id in sorted(groups):
        group = groups[event_id]
        by_hash: dict[str, list[dict]] = {}
        for event in group:
            by_hash.setdefault(event["source_content_hash"], []).append(event)
        if len(by_hash) == 1:
            selected = min(group, key=lambda value: value["source_record_ref"])
            admitted.append(selected)
            duplicate_count += len(group) - 1
            continue
        pairs = sorted((event["source_record_ref"], event["source_content_hash"]) for event in group)
        refs = [ref for ref, _ in pairs]
        hashes = sorted(by_hash)
        conflicts.append(
            {
                "event_id": event_id,
                "source_content_hashes": hashes,
                "source_record_refs": refs,
                "resolution": "NONE_FAIL_CLOSED",
            }
        )
        issues.append(
            make_issue(
                error_code="EVENT_ID_CONTENT_CONFLICT",
                source_record_ref=pairs[0][0],
                source_content_hash=pairs[0][1],
                observed_at=observed_at,
                blocking=True,
                evidence_ref=event_id,
            )
        )
    admitted.sort(key=lambda value: (value["occurred_at"], value["event_kind"], value["event_id"]))
    return admitted, conflicts, issues, duplicate_count


def load_source_snapshot(
    manifest_path: str | Path,
    *,
    manifest_schema_path: str | Path,
    m6_authority: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    raw_manifest = path.read_bytes()
    manifest = _parse_json_object(raw_manifest, "source manifest")
    schema_path = Path(manifest_schema_path).resolve()
    schema_bytes = schema_path.read_bytes()
    if len(schema_bytes) != SOURCE_MANIFEST_SCHEMA_BYTES or sha256_bytes(schema_bytes) != SOURCE_MANIFEST_SCHEMA_SHA256:
        raise ManifestIntegrityError("source manifest schema differs from the frozen candidate002 schema")
    schema = _parse_json_object(schema_bytes, "source manifest schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ManifestIntegrityError("source manifest schema is not valid Draft 2020-12") from exc
    schema_errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if schema_errors:
        first = schema_errors[0]
        raise ManifestIntegrityError(f"source manifest schema validation failed at {list(first.absolute_path)}")
    _validate_manifest(manifest)
    seen_paths: set[str] = set()
    seen_required_systems: set[str] = set()
    raw_events: list[dict] = []
    issues: list[dict] = []
    members: list[dict] = []
    for index, raw_member in enumerate(manifest["files"]):
        member = _validate_member(raw_member, index)
        relative = member["path"]
        path_key = relative.casefold()
        if path_key in seen_paths:
            raise ManifestIntegrityError(f"duplicate source member: {relative}")
        seen_paths.add(path_key)
        if member["required"]:
            seen_required_systems.add(member["source_system"])
        member_path = _safe_member(path.parent, relative)
        if not member_path.is_file():
            raise ManifestIntegrityError(f"source member missing: {relative}")
        raw = member_path.read_bytes()
        if sha256_bytes(raw) != member["sha256"] or len(raw) != member["bytes"]:
            raise ManifestIntegrityError(f"source member integrity mismatch: {relative}")
        # Independent streaming hash parity protects large-file behavior.
        if sha256_file(member_path) != member["sha256"]:
            raise ManifestIntegrityError(f"source member streaming hash mismatch: {relative}")
        records = _jsonl_records(raw, member)
        members.append(member)
        for line_no, raw_line in enumerate(records, start=1):
            ref = f"{relative}#line={line_no}"
            raw_hash = sha256_bytes(raw_line)
            try:
                record = _parse_json_object(raw_line, ref)
                raw_events.append(
                    adapt_record(
                        record,
                        source_system=member["source_system"],
                        source_record_ref=ref,
                        source_content_hash=raw_hash,
                        m6_authority=m6_authority,
                    )
                )
            except ManifestIntegrityError:
                issues.append(
                    make_issue(
                        error_code="UNPARSEABLE_JSON",
                        source_record_ref=ref,
                        source_content_hash=raw_hash,
                        observed_at=manifest["snapshot_observed_at"],
                        blocking=member["required"],
                    )
                )
            except SourceRecordError as exc:
                issues.append(
                    make_issue(
                        error_code=exc.error_code,
                        source_record_ref=ref,
                        source_content_hash=raw_hash,
                        observed_at=manifest["snapshot_observed_at"],
                        blocking=member["required"],
                        field_names=exc.field_names,
                    )
                )
    missing_systems = set(manifest["required_source_systems"]) - seen_required_systems
    if missing_systems:
        raise ManifestIntegrityError(f"required source systems absent as required members: {sorted(missing_systems)}")
    events, conflicts, conflict_issues, duplicate_count = _deduplicate(
        raw_events, manifest["snapshot_observed_at"]
    )
    issues.extend(conflict_issues)
    manifest_sha256 = sha256_bytes(raw_manifest)
    return {
        "manifest": manifest,
        "manifest_path": str(path),
        "manifest_schema_path": str(schema_path),
        "manifest_schema_sha256": SOURCE_MANIFEST_SCHEMA_SHA256,
        "manifest_sha256": manifest_sha256,
        "source_manifest_anchor": "SOURCE-MANIFEST-" + manifest_sha256[:24],
        "members": members,
        "events": events,
        "issues": sorted(issues, key=lambda value: value["issue_id"]),
        "conflicts": conflicts,
        "raw_event_count": len(raw_events),
        "admitted_event_count": len(events),
        "duplicate_event_count": duplicate_count,
    }
