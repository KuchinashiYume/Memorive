"""Load and verify the independent frozen authorities consumed by DataContracts."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .canonical import parse_datetime, require_safe_text, sha256_bytes, typed_payload_hash
from .errors import ManifestIntegrityError
from .initialization_pack import validate_common_object_pack

COMMON_SCHEMA_SHA256 = "7AFAC28C2132121800E87362371253B9F6518C9272D42D459F463CA3A13064BC"
COMMON_SCHEMA_BYTES = 11025
ACCEPTED_VALIDATOR_SHA256 = "26DE22DDE9C28ECF83DDDF52116E22D122BB83CCE58188D5C6D6707E7EFF3708"
EVIDENCE_REVIEW_POLICY = {
    "exact_trigger": "EvidenceReview校核结果",
    "reject_tag_only_spoof": True,
    "reject_trigger_name_spoof": "EVIDENCE_REVIEW_VERDICT",
    "report_token": "report=<id>",
    "report_token_must_resolve_exactly_once": True,
    "require_checked_count_greater_than": 0,
    "require_report_paper_id_equals_knowledge_admission_data_id": True,
    "require_report_sha256_binding": True,
    "require_report_verdict": "PASS",
    "string_or_boolean_coercion_allowed": False,
}
ACCEPTED_ACTORS = ["M05", "M07", "KNOWLEDGE_FEEDBACK", "RUNTIME_LOG", "RESEARCH_REPORTS", "RESEARCH-CONTRACTS-validation"]
DEPENDENCY_ACTORS = ["Retrieval", "Analysis"]
SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
EXPECTED_SOURCE_MANIFEST_PATHS = [
    "00_manifest/source_snapshot_conflict_manifest.json",
    "00_manifest/source_snapshot_runtime_log_contract_negatives_manifest.json",
    "00_manifest/source_snapshot_knowledge_admission_verification_spoof_manifest.json",
    "00_manifest/source_snapshot_manifest.json",
    "00_manifest/source_snapshot_optional_manifest.json",
    "00_manifest/source_snapshot_unparseable_manifest.json",
]


def _json_value(data: bytes, label: str) -> Any:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ManifestIntegrityError(f"{label} must not contain a BOM")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    def parse_int(text: str) -> int:
        value = int(text)
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("I-JSON integer range")
        return value

    def parse_float(text: str) -> float:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(value)

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_int=parse_int,
            parse_float=parse_float,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManifestIntegrityError(f"{label} is not strict UTF-8 I-JSON") from exc


def _object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = _json_value(raw, label)
    if not isinstance(value, dict):
        raise ManifestIntegrityError(f"{label} root must be an object")
    return value, raw


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ManifestIntegrityError(f"{label} fields mismatch")
    return value


def _text(value: Any, field: str) -> str:
    try:
        return require_safe_text(value, field)
    except (TypeError, ValueError) as exc:
        raise ManifestIntegrityError(str(exc)) from exc


def _hash(value: Any, field: str) -> str:
    result = _text(value, field)
    if not SHA256_RE.fullmatch(result):
        raise ManifestIntegrityError(f"{field} must be uppercase SHA-256")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestIntegrityError(f"{field} must be a positive integer")
    return value


def _resolve(root: Path, relative: Any, *, prefixes: tuple[str, ...]) -> Path:
    text = _text(relative, "authority path")
    if (
        "\\" in text
        or any(char in text for char in (":", "#", "?"))
        or text.startswith(("/", "//"))
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ManifestIntegrityError("authority path is not canonical relative text")
    if not text.startswith(prefixes):
        raise ManifestIntegrityError("authority path is outside its frozen subtree")
    path = (root / text).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestIntegrityError("authority path escapes successor root") from exc
    if not path.is_file():
        raise ManifestIntegrityError(f"authority member missing: {text}")
    return path


def _verify_file(path: Path, *, size: Any, sha256: Any, label: str) -> bytes:
    expected_size = _positive_int(size, f"{label}.bytes")
    expected_hash = _hash(sha256, f"{label}.sha256")
    raw = path.read_bytes()
    if len(raw) != expected_size or sha256_bytes(raw) != expected_hash:
        raise ManifestIntegrityError(f"{label} file binding mismatch")
    return raw


def load_common_schema(path: str | Path) -> tuple[dict[str, Any], str]:
    schema_path = Path(path).resolve()
    raw = schema_path.read_bytes()
    if len(raw) != COMMON_SCHEMA_BYTES or sha256_bytes(raw) != COMMON_SCHEMA_SHA256:
        raise ManifestIntegrityError("Initialization common schema differs from the accepted snapshot")
    value = _json_value(raw, "Initialization common schema")
    if not isinstance(value, dict):
        raise ManifestIntegrityError("Initialization common schema root must be an object")
    return value, COMMON_SCHEMA_SHA256


def load_evidence_review_authority(path: str | Path, *, successor_root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    manifest, raw = _object(Path(path).resolve(), "KNOWLEDGE_ADMISSION/EVIDENCE_REVIEW authority")
    _exact(manifest, {"authority_id", "data_classification", "reports", "resolver_policy", "schema_version"}, "KNOWLEDGE_ADMISSION/EVIDENCE_REVIEW authority")
    if manifest["schema_version"] != "1.0" or manifest["data_classification"] != "PUBLIC_SAFE_SYNTHETIC":
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION/EVIDENCE_REVIEW authority identity mismatch")
    if manifest["resolver_policy"] != EVIDENCE_REVIEW_POLICY:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION/EVIDENCE_REVIEW resolver policy differs from the frozen exact policy")
    authority_id = _text(manifest["authority_id"], "authority_id")
    reports = manifest["reports"]
    if not isinstance(reports, list) or not reports:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION/EVIDENCE_REVIEW authority reports must be non-empty")
    normalized: dict[str, dict[str, Any]] = {}
    expected_report_fields = {
        "card", "checked_at", "checked_count", "checked_locations", "confidence",
        "distill_model", "paper_id", "problems", "report_id", "same_source_reason",
        "upgrade_pending", "verdict", "verify_model",
    }
    for index, entry in enumerate(reports):
        entry = _exact(entry, {"bytes", "expected", "path", "report_id", "sha256"}, f"reports[{index}]")
        expected = _exact(entry["expected"], {"checked_count_minimum", "paper_id", "verdict"}, f"reports[{index}].expected")
        report_id = _text(entry["report_id"], "report_id")
        if report_id in normalized:
            raise ManifestIntegrityError("duplicate EVIDENCE_REVIEW report_id")
        report_path = _resolve(
            successor_root,
            entry["path"],
            prefixes=("00_manifest/source_snapshot/evidence_review_reports/",),
        )
        report_raw = _verify_file(report_path, size=entry["bytes"], sha256=entry["sha256"], label=f"reports[{index}]")
        report = _json_value(report_raw, f"EVIDENCE_REVIEW report {report_id}")
        if not isinstance(report, dict) or set(report) != expected_report_fields:
            raise ManifestIntegrityError("EVIDENCE_REVIEW report fields mismatch")
        checked_count = _positive_int(report.get("checked_count"), "checked_count")
        minimum = _positive_int(expected["checked_count_minimum"], "checked_count_minimum")
        if (
            report.get("report_id") != report_id
            or report.get("paper_id") != expected["paper_id"]
            or report.get("verdict") != expected["verdict"]
            or report.get("verdict") != "PASS"
            or checked_count < minimum
        ):
            raise ManifestIntegrityError("EVIDENCE_REVIEW report semantic binding mismatch")
        normalized[report_id] = {
            "report_id": report_id,
            "paper_id": report["paper_id"],
            "verdict": report["verdict"],
            "checked_count": checked_count,
            "report_sha256": _hash(entry["sha256"], "report.sha256"),
            "authority_ref": f"authority://{authority_id}#/reports/{index}",
        }
    return normalized, sha256_bytes(raw)


def _pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer.startswith("/"):
        raise ManifestIntegrityError("JSON Pointer must be absolute")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):
                raise ManifestIntegrityError("JSON Pointer list step is unresolved")
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ManifestIntegrityError("JSON Pointer object step is unresolved")
    return current


def load_actor_authority(
    path: str | Path,
    *,
    successor_root: Path,
    snapshot: dict[str, Any],
    evidence_review_authority: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str], str]:
    manifest, raw = _object(Path(path).resolve(), "Initialization actor authority")
    _exact(
        manifest,
        {
            "accepted_validator_snapshot", "authority_id", "dependency_task_actor_source",
            "dynamic_expansion_allowed", "knowledge_admission_verified_common_reference_bindings",
            "resolution_policy", "resolved_actor_locators", "schema_version",
        },
        "Initialization actor authority",
    )
    if manifest["schema_version"] != "1.0" or manifest["dynamic_expansion_allowed"] is not False:
        raise ManifestIntegrityError("Initialization actor authority policy mismatch")
    accepted = _exact(
        manifest["accepted_validator_snapshot"],
        {"bytes", "constant_name", "known_actor_locators", "path", "sha256"},
        "accepted_validator_snapshot",
    )
    if accepted["constant_name"] != "KNOWN_ACTOR_LOCATORS" or accepted["known_actor_locators"] != ACCEPTED_ACTORS:
        raise ManifestIntegrityError("accepted actor oracle set mismatch")
    validator_path = _resolve(successor_root, accepted["path"], prefixes=("validator/authority_snapshots/",))
    _verify_file(validator_path, size=accepted["bytes"], sha256=accepted["sha256"], label="accepted_validator_snapshot")
    if accepted["sha256"] != ACCEPTED_VALIDATOR_SHA256:
        raise ManifestIntegrityError("accepted Initialization validator hash mismatch")
    dependency = _exact(
        manifest["dependency_task_actor_source"],
        {"anchors", "bytes", "exit_handoff", "path", "sha256", "task_id", "task_index"},
        "dependency_task_actor_source",
    )
    if dependency["task_id"] != "DataContracts" or dependency["task_index"] != 1 or dependency["exit_handoff"] != DEPENDENCY_ACTORS:
        raise ManifestIntegrityError("DataContracts dependency actor identity mismatch")
    dep_path = _resolve(successor_root, dependency["path"], prefixes=("repo/",))
    dep_raw = _verify_file(dep_path, size=dependency["bytes"], sha256=dependency["sha256"], label="dependency_task_actor_source")
    dep_doc = _json_value(dep_raw, "Initialization dependency matrix")
    anchors = dependency["anchors"]
    if not isinstance(anchors, list) or len(anchors) != len(DEPENDENCY_ACTORS):
        raise ManifestIntegrityError("dependency actor anchors mismatch")
    for entry, expected_locator in zip(anchors, DEPENDENCY_ACTORS):
        entry = _exact(entry, {"json_pointer", "locator"}, "dependency anchor")
        if entry["locator"] != expected_locator or _pointer(dep_doc, entry["json_pointer"]) != expected_locator:
            raise ManifestIntegrityError("dependency actor JSON Pointer binding mismatch")
    resolved = ACCEPTED_ACTORS + DEPENDENCY_ACTORS
    if manifest["resolved_actor_locators"] != resolved:
        raise ManifestIntegrityError("resolved actor locator union mismatch")
    if manifest["resolution_policy"] != "accepted_validator_set_union_exact_data_contracts_dependency_anchors":
        raise ManifestIntegrityError("actor resolution policy mismatch")
    bindings = _exact(
        manifest["knowledge_admission_verified_common_reference_bindings"],
        {"refs", "resolution_scope", "runtime_collection_allowed", "source_record"},
        "knowledge_admission_verified_common_reference_bindings",
    )
    if bindings["resolution_scope"] != "ONLY_THE_EXACT_BOUND_KNOWLEDGE_ADMISSION_RECORD" or bindings["runtime_collection_allowed"] is not False:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified common-reference scope mismatch")
    source = _exact(
        bindings["source_record"],
        {"data_id", "exact_trigger", "path", "record_hash_scope", "record_index_one_based", "record_sha256"},
        "KNOWLEDGE_ADMISSION verified source_record",
    )
    if source["exact_trigger"] != "EvidenceReview校核结果" or source["record_hash_scope"] != "UTF8_JSONL_LINE_BYTES_EXCLUDING_TERMINATING_LF":
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified source trigger/hash scope mismatch")
    source_path = _text(source["path"], "KNOWLEDGE_ADMISSION verified source path")
    source_line = _positive_int(source["record_index_one_based"], "KNOWLEDGE_ADMISSION verified record index")
    source_hash = _hash(source["record_sha256"], "KNOWLEDGE_ADMISSION verified record sha256")
    member = next((item for item in snapshot["members"] if item["path"] == source_path and item["source_system"] == "KNOWLEDGE_ADMISSION"), None)
    if member and (source_line > member["record_count"] or member["record_sha256"][source_line - 1] != source_hash):
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified common ref does not bind a frozen source line")
    source_event = next((item for item in snapshot["events"] if item["source_content_hash"] == source_hash), None)
    if source_event is None and member is None:
        frozen_path = _resolve(
            successor_root,
            "00_manifest/" + source_path,
            prefixes=("00_manifest/source_snapshot/",),
        )
        frozen_raw = frozen_path.read_bytes()
        if b"\r" in frozen_raw or not frozen_raw.endswith(b"\n"):
            raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified source is not LF JSONL")
        records = frozen_raw[:-1].split(b"\n")
        if source_line > len(records) or sha256_bytes(records[source_line - 1]) != source_hash:
            raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified source bytes do not match authority")
        from .adapters import adapt_record

        source_event = adapt_record(
            _json_value(records[source_line - 1], "KNOWLEDGE_ADMISSION verified source row"),
            source_system="KNOWLEDGE_ADMISSION",
            source_record_ref=f"{source_path}#line={source_line}",
            source_content_hash=source_hash,
            evidence_review_authority=evidence_review_authority,
        )
    if (
        not source_event
        or source_event["source_record_ref"] != f"{source_path}#line={source_line}"
        or source_event["subject_refs"] != [source["data_id"]]
        or source_event["source_details"].get("trigger") != source["exact_trigger"]
    ):
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified common ref does not bind the admitted event")
    refs = bindings["refs"]
    if not isinstance(refs, list) or len(refs) != 2:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified common refs must contain exactly two entries")
    authority_entry = _exact(
        refs[0], {"authority", "json_pointer", "kind", "ref", "report_id"}, "KNOWLEDGE_ADMISSION authority ref"
    )
    if authority_entry["kind"] != "frozen_authority_entry" or authority_entry["json_pointer"] != "/reports/0":
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION authority reference kind/pointer mismatch")
    authority_file = _exact(authority_entry["authority"], {"bytes", "path", "sha256"}, "KNOWLEDGE_ADMISSION authority file")
    authority_path = _resolve(successor_root, authority_file["path"], prefixes=("00_manifest/",))
    authority_raw = _verify_file(authority_path, size=authority_file["bytes"], sha256=authority_file["sha256"], label="KNOWLEDGE_ADMISSION authority file")
    authority_doc = _json_value(authority_raw, "KNOWLEDGE_ADMISSION authority file")
    report_entry = _pointer(authority_doc, authority_entry["json_pointer"])
    if not isinstance(report_entry, dict) or report_entry.get("report_id") != authority_entry["report_id"]:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION authority JSON Pointer/report ID mismatch")
    expected_authority_ref = f"authority://{authority_doc['authority_id']}#/reports/0"
    if authority_entry["ref"] != expected_authority_ref:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION authority reference text mismatch")
    hash_entry = _exact(refs[1], {"kind", "ref", "report"}, "KNOWLEDGE_ADMISSION report hash ref")
    report_file = _exact(hash_entry["report"], {"bytes", "path", "sha256"}, "KNOWLEDGE_ADMISSION report file")
    report_path = _resolve(successor_root, report_file["path"], prefixes=("00_manifest/source_snapshot/evidence_review_reports/",))
    _verify_file(report_path, size=report_file["bytes"], sha256=report_file["sha256"], label="KNOWLEDGE_ADMISSION report file")
    expected_hash_ref = f"sha256:file_bytes:{report_file['sha256']}"
    if hash_entry["kind"] != "exact_report_raw_file_hash" or hash_entry["ref"] != expected_hash_ref:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION report hash reference mismatch")
    frozen_refs = {authority_entry["ref"], hash_entry["ref"]}
    common_payload = source_event.get("common_object", {}).get("payload", {})
    expected_common_refs = [source_event["source_record_ref"], authority_entry["ref"], hash_entry["ref"]]
    source_details = source_event.get("source_details", {})
    if (
        source_event.get("verification_status") != "verified_by_authoritative_evidence_review_report"
        or source_details.get("evidence_review_report_id") != authority_entry["report_id"]
        or source_details.get("evidence_review_authority_bound") is not True
        or not frozen_refs <= set(source_event.get("evidence_refs", []))
        or common_payload.get("source_refs") != expected_common_refs
        or common_payload.get("provenance_refs") != expected_common_refs
    ):
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified event outer/common authority binding mismatch")
    return set(resolved), frozen_refs, sha256_bytes(raw)


def load_common_reference_authority(
    path: str | Path,
    *,
    successor_root: Path,
    actor_locators: set[str],
    frozen_knowledge_admission_refs: set[str],
) -> tuple[set[str], str]:
    """Verify the finite Initialization reference universe; never collect runtime refs."""

    manifest, raw = _object(Path(path).resolve(), "common-object reference authority")
    _exact(
        manifest,
        {
            "actor_refs", "all_common_refs", "authority_id",
            "candidate_output_may_expand_authority", "knowledge_admission_verified_refs",
            "prior_refs", "runtime_collection_allowed", "schema_version",
            "source_file_anchors", "source_line_refs", "source_manifest_anchors",
        },
        "common-object reference authority",
    )
    if (
        manifest["schema_version"] != "1.0"
        or manifest["runtime_collection_allowed"] is not False
        or manifest["candidate_output_may_expand_authority"] is not False
    ):
        raise ManifestIntegrityError("common-object reference authority policy mismatch")

    file_anchors = manifest["source_file_anchors"]
    if not isinstance(file_anchors, list) or not file_anchors:
        raise ManifestIntegrityError("source_file_anchors must be non-empty")
    line_truth: dict[str, tuple[str, list[str]]] = {}
    for index, entry in enumerate(file_anchors):
        entry = _exact(entry, {"bytes", "path", "sha256", "source_system"}, f"source_file_anchors[{index}]")
        system = _text(entry["source_system"], "source_system")
        if system not in {"KNOWLEDGE_ADMISSION", "RUNTIME_LOG", "ARTIFACT_REGISTRY"}:
            raise ManifestIntegrityError("source file anchor system mismatch")
        target = _resolve(successor_root, entry["path"], prefixes=("00_manifest/source_snapshot/",))
        source_raw = _verify_file(target, size=entry["bytes"], sha256=entry["sha256"], label=f"source_file_anchors[{index}]")
        if source_raw.startswith(b"\xef\xbb\xbf") or b"\r" in source_raw or not source_raw.endswith(b"\n"):
            raise ManifestIntegrityError("frozen source authority file is not LF JSONL")
        records = source_raw[:-1].split(b"\n")
        if not records or any(not value for value in records):
            raise ManifestIntegrityError("frozen source authority file has empty records")
        source_path = _text(entry["path"], "source anchor path").removeprefix("00_manifest/")
        if source_path in line_truth:
            raise ManifestIntegrityError("duplicate source file anchor path")
        line_truth[source_path] = (system, [sha256_bytes(value) for value in records])

    source_line_refs = manifest["source_line_refs"]
    if not isinstance(source_line_refs, list) or not source_line_refs:
        raise ManifestIntegrityError("source_line_refs must be non-empty")
    observed_line_refs: set[str] = set()
    observed_line_keys: set[tuple[str, int]] = set()
    for index, entry in enumerate(source_line_refs):
        entry = _exact(
            entry,
            {"line_no", "path", "record_hash_scope", "record_sha256", "ref", "source_system"},
            f"source_line_refs[{index}]",
        )
        source_path = _text(entry["path"], "source line path")
        line_no = _positive_int(entry["line_no"], "source line number")
        truth = line_truth.get(source_path)
        if (
            truth is None
            or entry["source_system"] != truth[0]
            or entry["record_hash_scope"] != "UTF8_JSONL_LINE_BYTES_EXCLUDING_TERMINATING_LF"
            or line_no > len(truth[1])
            or _hash(entry["record_sha256"], "source line hash") != truth[1][line_no - 1]
            or entry["ref"] != f"{source_path}#line={line_no}"
        ):
            raise ManifestIntegrityError("source line reference binding mismatch")
        key = (source_path, line_no)
        if key in observed_line_keys or entry["ref"] in observed_line_refs:
            raise ManifestIntegrityError("duplicate source line reference")
        observed_line_keys.add(key)
        observed_line_refs.add(entry["ref"])
    expected_line_keys = {
        (source_path, line_no)
        for source_path, (_, hashes) in line_truth.items()
        for line_no in range(1, len(hashes) + 1)
    }
    if observed_line_keys != expected_line_keys:
        raise ManifestIntegrityError("source line authority is not exact-complete")

    manifest_anchors = manifest["source_manifest_anchors"]
    if not isinstance(manifest_anchors, list) or len(manifest_anchors) != len(EXPECTED_SOURCE_MANIFEST_PATHS):
        raise ManifestIntegrityError("source_manifest_anchors count mismatch")
    observed_manifest_paths: list[str] = []
    observed_manifest_refs: set[str] = set()
    for index, manifest_anchor in enumerate(manifest_anchors):
        manifest_anchor = _exact(
            manifest_anchor, {"derivation", "ref", "target"}, f"source_manifest_anchors[{index}]"
        )
        target = _exact(manifest_anchor["target"], {"bytes", "path", "sha256"}, "source_manifest_anchor.target")
        target_path = _resolve(successor_root, target["path"], prefixes=("00_manifest/",))
        _verify_file(target_path, size=target["bytes"], sha256=target["sha256"], label=f"source_manifest_anchors[{index}].target")
        expected_manifest_ref = "SOURCE-MANIFEST-" + _hash(target["sha256"], "source manifest hash")[:24]
        if (
            manifest_anchor["derivation"] != "SOURCE-MANIFEST- + first 24 uppercase hex of raw file SHA-256"
            or manifest_anchor["ref"] != expected_manifest_ref
            or expected_manifest_ref in observed_manifest_refs
        ):
            raise ManifestIntegrityError("source manifest reference derivation/uniqueness mismatch")
        observed_manifest_paths.append(target["path"])
        observed_manifest_refs.add(expected_manifest_ref)
    if observed_manifest_paths != EXPECTED_SOURCE_MANIFEST_PATHS:
        raise ManifestIntegrityError("source manifest anchor path/order exact-set mismatch")

    knowledge_admission_entries = manifest["knowledge_admission_verified_refs"]
    if not isinstance(knowledge_admission_entries, list) or len(knowledge_admission_entries) != 2:
        raise ManifestIntegrityError("knowledge_admission_verified_refs must contain exactly two bindings")
    observed_knowledge_admission_refs: set[str] = set()
    for entry in knowledge_admission_entries:
        entry = _exact(entry, {"binding", "ref"}, "knowledge_admission_verified_ref")
        ref = _text(entry["ref"], "knowledge_admission verified ref")
        if not isinstance(entry["binding"], dict) or entry["binding"].get("ref") != ref:
            raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified ref/binding mismatch")
        observed_knowledge_admission_refs.add(ref)
    if observed_knowledge_admission_refs != frozen_knowledge_admission_refs:
        raise ManifestIntegrityError("KNOWLEDGE_ADMISSION verified ref set differs from actor authority")

    actor_entries = manifest["actor_refs"]
    if not isinstance(actor_entries, list):
        raise ManifestIntegrityError("actor_refs must be a list")
    observed_actors: set[str] = set()
    for entry in actor_entries:
        entry = _exact(entry, {"kind", "ref", "source", "source_selector"}, "actor_ref")
        actor = _text(entry["ref"], "actor ref")
        source = _exact(entry["source"], {"bytes", "path", "sha256"}, "actor_ref.source")
        source_path = _resolve(successor_root, source["path"], prefixes=("validator/authority_snapshots/", "repo/"))
        _verify_file(source_path, size=source["bytes"], sha256=source["sha256"], label=f"actor_ref:{actor}")
        if actor in observed_actors:
            raise ManifestIntegrityError("duplicate actor reference")
        observed_actors.add(actor)
    if observed_actors != actor_locators:
        raise ManifestIntegrityError("actor reference universe mismatch")

    prior = _exact(manifest["prior_refs"], {"authority", "common_object_pack", "refs"}, "prior_refs")
    for field, prefixes in (
        ("authority", ("00_manifest/",)),
        ("common_object_pack", ("00_manifest/source_snapshot/prior_digest/",)),
    ):
        anchor = _exact(prior[field], {"bytes", "path", "sha256"}, f"prior_refs.{field}")
        anchor_path = _resolve(successor_root, anchor["path"], prefixes=prefixes)
        _verify_file(anchor_path, size=anchor["bytes"], sha256=anchor["sha256"], label=f"prior_refs.{field}")
    prior_refs = prior["refs"]
    if not isinstance(prior_refs, list) or not prior_refs or any(not isinstance(value, str) or not value for value in prior_refs):
        raise ManifestIntegrityError("prior refs are invalid")
    if len(prior_refs) != len(set(prior_refs)):
        raise ManifestIntegrityError("duplicate prior ref")

    expected_refs = sorted(observed_line_refs | observed_manifest_refs | observed_knowledge_admission_refs | set(prior_refs))
    all_refs = manifest["all_common_refs"]
    if all_refs != expected_refs:
        raise ManifestIntegrityError("all_common_refs is not the exact frozen union")
    return set(all_refs), sha256_bytes(raw)


def load_historical_authority(
    path: str | Path,
    *,
    successor_root: Path,
    snapshot: dict[str, Any],
    prior_digest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str], str]:
    manifest, raw = _object(Path(path).resolve(), "historical cutoff authority")
    _exact(
        manifest,
        {"authority_id", "bindings", "inference_without_exact_binding_allowed", "record_hash_scope", "schema_version"},
        "historical cutoff authority",
    )
    if (
        manifest["schema_version"] != "1.0"
        or manifest["inference_without_exact_binding_allowed"] is not False
        or manifest["record_hash_scope"] != "UTF8_JSONL_LINE_BYTES_EXCLUDING_TERMINATING_LF"
    ):
        raise ManifestIntegrityError("historical authority policy mismatch")
    authority_id = _text(manifest["authority_id"], "authority_id")
    bindings = manifest["bindings"]
    if not isinstance(bindings, list) or not bindings:
        raise ManifestIntegrityError("historical bindings must be non-empty")
    normalized: dict[str, dict[str, Any]] = {}
    reference_aliases: set[str] = set()
    event_by_hash = {event["source_content_hash"]: event for event in snapshot["events"]}
    member_by_path = {member["path"]: member for member in snapshot["members"]}
    binding_fields = {
        "classification", "current_reporting_window", "event_time", "event_time_field",
        "first_observed_at", "first_observed_at_field", "original_common_payload_hash",
        "original_digest_id", "original_digest_raw_file", "original_window", "source_path",
        "source_record_id", "source_record_index_one_based", "source_record_sha256", "source_system",
    }
    for index, binding in enumerate(bindings):
        binding = _exact(binding, binding_fields, f"bindings[{index}]")
        if binding["classification"] != "LATE_ARRIVAL_FROM_PRIOR_WINDOW" or binding["source_system"] != "RUNTIME_LOG":
            raise ManifestIntegrityError("historical binding classification/source mismatch")
        if binding["event_time_field"] != "finished_at" or binding["first_observed_at_field"] != "created_at":
            raise ManifestIntegrityError("historical timestamp field authority mismatch")
        source_hash = _hash(binding["source_record_sha256"], "source_record_sha256")
        if source_hash in normalized:
            raise ManifestIntegrityError("duplicate historical source record hash")
        source_path = _text(binding["source_path"], "source_path")
        line_no = _positive_int(binding["source_record_index_one_based"], "source_record_index_one_based")
        frozen_source_path = _resolve(
            successor_root,
            "00_manifest/" + source_path,
            prefixes=("00_manifest/source_snapshot/",),
        )
        frozen_raw = frozen_source_path.read_bytes()
        if frozen_raw.startswith(b"\xef\xbb\xbf") or b"\r" in frozen_raw or not frozen_raw.endswith(b"\n"):
            raise ManifestIntegrityError("historical source authority is not LF JSONL")
        frozen_records = frozen_raw[:-1].split(b"\n")
        if line_no > len(frozen_records) or sha256_bytes(frozen_records[line_no - 1]) != source_hash:
            raise ManifestIntegrityError("historical source line hash does not bind frozen bytes")
        member = member_by_path.get(source_path)
        if member and (line_no > member["record_count"] or member["record_sha256"][line_no - 1] != source_hash):
            raise ManifestIntegrityError("historical source line does not resolve in source manifest")
        event = event_by_hash.get(source_hash)
        current = _exact(binding["current_reporting_window"], {"cutoff_at", "end_exclusive", "start"}, "current_reporting_window")
        original = _exact(binding["original_window"], {"cutoff_at", "end_exclusive", "start"}, "original_window")
        parsed_windows = []
        for window in (current, original):
            parsed = [parse_datetime(window[field], field_name=field) for field in ("start", "end_exclusive", "cutoff_at")]
            if not parsed[0] < parsed[1] <= parsed[2]:
                raise ManifestIntegrityError("historical window ordering mismatch")
            parsed_windows.append(parsed)
        current_start, current_end, current_cutoff = parsed_windows[0]
        original_start, original_end, original_cutoff = parsed_windows[1]
        event_time = parse_datetime(binding["event_time"], field_name="event_time")
        observed_time = parse_datetime(binding["first_observed_at"], field_name="first_observed_at")
        if not (
            original_start <= event_time < original_end
            and original_cutoff < observed_time <= current_cutoff
            and current_start <= observed_time < current_end
        ):
            raise ManifestIntegrityError("historical late-arrival ordering mismatch")
        typed_hash = _exact(
            binding["original_common_payload_hash"],
            {"algorithm", "hash_kind", "scope", "value"},
            "original_common_payload_hash",
        )
        if (
            typed_hash["algorithm"] != "sha256"
            or typed_hash["hash_kind"] != "rfc8785_jcs_sha256"
            or typed_hash["scope"] != "PAYLOAD_EXCLUDING_CONTENT_HASH"
        ):
            raise ManifestIntegrityError("historical common payload hash kind mismatch")
        original_common_hash = _hash(typed_hash["value"], "original_common_payload_hash.value")
        raw_file = _exact(binding["original_digest_raw_file"], {"bytes", "hash_scope", "path", "sha256"}, "original_digest_raw_file")
        if raw_file["hash_scope"] != "RAW_FILE_BYTES_SHA256":
            raise ManifestIntegrityError("historical original digest hash scope mismatch")
        raw_path = _resolve(successor_root, raw_file["path"], prefixes=("00_manifest/source_snapshot/prior_digest/",))
        original_raw = _verify_file(raw_path, size=raw_file["bytes"], sha256=raw_file["sha256"], label="original_digest_raw_file")
        original_doc = _json_value(original_raw, "historical original digest")
        if not isinstance(original_doc, dict) or original_doc.get("payload", {}).get("object_id") != binding["original_digest_id"]:
            raise ManifestIntegrityError("historical original digest ID mismatch")
        if original_doc.get("payload", {}).get("content_hash", {}).get("value") != original_common_hash:
            raise ManifestIntegrityError("historical original common hash mismatch")
        if typed_payload_hash(original_doc["payload"]).get("value") != original_common_hash:
            raise ManifestIntegrityError("historical original common typed hash is stale")
        if (
            prior_digest.get("digest_id") != binding["original_digest_id"]
            or prior_digest.get("raw_sha256") != raw_file["sha256"]
            or prior_digest.get("common_payload_hash") != original_common_hash
            or prior_digest.get("common_object") != original_doc
        ):
            raise ManifestIntegrityError("historical authority differs from prior-digest authority")
        # A variant source manifest may intentionally omit this frozen row.
        # Its authority is still verified above, but it contributes no late
        # event unless the centrally admitted pack contains the exact row.
        if event is None and member is None:
            reference_aliases.add(f"RUNTIME_LOG:{binding['source_record_id']}")
            continue
        if not event or event["source_record_ref"] != f"{source_path}#line={line_no}" or event["source_system"] != "RUNTIME_LOG":
            raise ManifestIntegrityError("historical source event does not resolve in admitted pack")
        if binding["source_record_id"] not in event["subject_refs"]:
            raise ManifestIntegrityError("historical source_record_id does not match event subjects")
        if binding["event_time"] != event["occurred_at"] or binding["first_observed_at"] != event["observed_at"]:
            raise ManifestIntegrityError("historical event/observation time mismatch")
        current_ref = f"daily:{current['start']}..{current['end_exclusive']}"
        original_ref = f"daily:{original['start']}..{original['end_exclusive']}"
        normalized[source_hash] = {
            "source_content_hash": source_hash,
            "event_id": event["event_id"],
            "occurred_at": event["occurred_at"],
            "observed_at": event["observed_at"],
            "original_window_ref": original_ref,
            "original_cutoff_at": original["cutoff_at"],
            "original_digest_id": binding["original_digest_id"],
            "original_digest_raw_sha256": _hash(raw_file["sha256"], "original_digest_raw_file.sha256"),
            "original_common_payload_hash": original_common_hash,
            "current_window_ref": current_ref,
            "authority_ref": f"HISTORY-AUTHORITY:{authority_id}:{source_hash}",
        }
        reference_aliases.add(f"RUNTIME_LOG:{binding['source_record_id']}")
    return normalized, reference_aliases, sha256_bytes(raw)


def load_prior_digest_authority(
    path: str | Path,
    *,
    successor_root: Path,
    common_schema: dict[str, Any],
    actor_locators: set[str],
    authority_refs: set[str],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    manifest, raw = _object(Path(path).resolve(), "prior digest authority")
    _exact(
        manifest,
        {"authority_id", "authority_refs", "common_object_pack", "dynamic_prior_discovery_allowed", "prior_digest", "required_successor_binding", "schema_version"},
        "prior digest authority",
    )
    if manifest["schema_version"] != "1.0" or manifest["dynamic_prior_discovery_allowed"] is not False:
        raise ManifestIntegrityError("prior digest authority policy mismatch")
    refs = manifest["authority_refs"]
    if not isinstance(refs, list) or len(refs) != 2:
        raise ManifestIntegrityError("prior authority_refs must contain the exact two frozen refs")
    resolved_ref_texts: list[str] = []
    for index, entry in enumerate(refs):
        if not isinstance(entry, dict):
            raise ManifestIntegrityError("prior authority ref must be an object")
        kind = entry.get("kind")
        if kind == "raw_file":
            entry = _exact(entry, {"kind", "reference", "target"}, f"authority_refs[{index}]")
            target = _exact(entry["target"], {"bytes", "path", "sha256"}, "prior raw authority target")
            target_path = _resolve(successor_root, target["path"], prefixes=("00_manifest/",))
            _verify_file(target_path, size=target["bytes"], sha256=target["sha256"], label="prior raw authority target")
            if entry["reference"] != "source_snapshot_manifest.json":
                raise ManifestIntegrityError("prior raw authority reference mismatch")
        elif kind == "jsonl_record":
            entry = _exact(
                entry,
                {"kind", "record_hash_scope", "record_id", "record_index_one_based", "record_sha256", "reference", "target"},
                f"authority_refs[{index}]",
            )
            if entry["record_hash_scope"] != "UTF8_JSONL_LINE_BYTES_EXCLUDING_TERMINATING_LF":
                raise ManifestIntegrityError("prior JSONL authority hash scope mismatch")
            target = _exact(entry["target"], {"bytes", "path", "sha256"}, "prior JSONL authority target")
            target_path = _resolve(successor_root, target["path"], prefixes=("00_manifest/source_snapshot/",))
            target_raw = _verify_file(target_path, size=target["bytes"], sha256=target["sha256"], label="prior JSONL authority target")
            if b"\r" in target_raw or not target_raw.endswith(b"\n"):
                raise ManifestIntegrityError("prior JSONL authority target is not LF JSONL")
            line_no = _positive_int(entry["record_index_one_based"], "prior record index")
            records = target_raw[:-1].split(b"\n")
            if line_no > len(records) or sha256_bytes(records[line_no - 1]) != _hash(entry["record_sha256"], "prior record hash"):
                raise ManifestIntegrityError("prior JSONL authority line mismatch")
            record = _json_value(records[line_no - 1], "prior JSONL authority record")
            if not isinstance(record, dict) or record.get("record_id") != entry["record_id"]:
                raise ManifestIntegrityError("prior JSONL authority record ID mismatch")
            if entry["reference"] != f"RUNTIME_LOG:{entry['record_id']}":
                raise ManifestIntegrityError("prior JSONL authority reference text mismatch")
        else:
            raise ManifestIntegrityError("unsupported prior authority ref kind")
        resolved_ref_texts.append(_text(entry["reference"], "prior authority reference"))
    if len(resolved_ref_texts) != len(set(resolved_ref_texts)) or not set(resolved_ref_texts) <= authority_refs:
        raise ManifestIntegrityError("prior authority refs are duplicate or outside frozen reference authority")
    pack_info = _exact(manifest["common_object_pack"], {"bytes", "object_ids", "path", "record_count", "sha256"}, "common_object_pack")
    pack_path = _resolve(successor_root, pack_info["path"], prefixes=("00_manifest/source_snapshot/prior_digest/",))
    pack_raw = _verify_file(pack_path, size=pack_info["bytes"], sha256=pack_info["sha256"], label="common_object_pack")
    pack = _json_value(pack_raw, "prior common object pack")
    if not isinstance(pack, list) or len(pack) != _positive_int(pack_info["record_count"], "record_count"):
        raise ManifestIntegrityError("prior common pack record count mismatch")
    object_ids = [item.get("payload", {}).get("object_id") for item in pack if isinstance(item, dict)]
    if object_ids != pack_info["object_ids"]:
        raise ManifestIntegrityError("prior common pack object ID sequence mismatch")
    validate_common_object_pack(
        pack,
        common_schema=common_schema,
        actor_locators=actor_locators,
        authority_refs=authority_refs,
        data_contracts_exact=False,
    )
    prior = _exact(
        manifest["prior_digest"],
        {"common_payload_hash", "digest_kind", "object_id", "raw_file", "revision", "state", "supersedes"},
        "prior_digest",
    )
    if prior["digest_kind"] != "daily" or prior["revision"] != 1 or prior["state"] != "draft" or prior["supersedes"] is not None:
        raise ManifestIntegrityError("prior digest state/revision identity mismatch")
    successor = _exact(
        manifest["required_successor_binding"],
        {"propagate_to_common_object", "reuse_prior_object_id", "revision", "state", "supersedes"},
        "required_successor_binding",
    )
    if successor != {
        "propagate_to_common_object": True,
        "reuse_prior_object_id": False,
        "revision": 2,
        "state": "validated",
        "supersedes": prior["object_id"],
    }:
        raise ManifestIntegrityError("required successor correction binding mismatch")
    digest_wrapper = next((item for item in pack if item.get("payload", {}).get("object_id") == prior["object_id"]), None)
    if not isinstance(digest_wrapper, dict) or digest_wrapper.get("object_type") != "ChangeDigest":
        raise ManifestIntegrityError("prior ChangeDigest common object missing from pack")
    payload = digest_wrapper["payload"]
    common_hash = _exact(prior["common_payload_hash"], {"algorithm", "hash_kind", "scope", "value"}, "prior common_payload_hash")
    if common_hash != payload.get("content_hash") or typed_payload_hash(payload) != common_hash:
        raise ManifestIntegrityError("prior common payload hash is stale")
    raw_file = _exact(prior["raw_file"], {"bytes", "path", "sha256"}, "prior raw_file")
    raw_path = _resolve(successor_root, raw_file["path"], prefixes=("00_manifest/source_snapshot/prior_digest/",))
    prior_raw = _verify_file(raw_path, size=raw_file["bytes"], sha256=raw_file["sha256"], label="prior raw_file")
    prior_doc = _json_value(prior_raw, "prior raw digest")
    if prior_doc != digest_wrapper:
        raise ManifestIntegrityError("prior raw digest does not exactly match the pack object")
    normalized = {
        "digest_id": prior["object_id"],
        "digest_kind": prior["digest_kind"],
        "revision": prior["revision"],
        "raw_sha256": _hash(raw_file["sha256"], "prior raw sha256"),
        "common_payload_hash": _hash(common_hash["value"], "prior common hash"),
        "common_object": digest_wrapper,
    }
    return normalized, digest_wrapper, sha256_bytes(raw)
