"""Deterministic ChangeDigest construction over the unique admitted pack."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .adapters import FAILURE_SEMANTICS, IMMUTABLE_FIELDS
from .canonical import canonical_json_bytes, parse_datetime, sha256_bytes, typed_payload_hash
from .errors import ResearchReportsError
from .issues import make_issue
from .policy import REPORT_TIMEZONE, SECTION_TITLES, original_window_ref, validate_window


def _generator_version() -> str:
    package_root = Path(__file__).resolve().parent
    members = []
    for path in sorted(
        (
            value
            for value in package_root.rglob("*")
            if value.is_file()
            and value.suffix in {".py", ".json", ".tmpl"}
            and "__pycache__" not in value.parts
        ),
        key=lambda value: value.relative_to(package_root).as_posix(),
    ):
        members.append(
            {
                "path": path.relative_to(package_root).as_posix(),
                "sha256": sha256_bytes(path.read_bytes()),
            }
        )
    return "research_reports-research-changelog/1.1.0+sha256." + sha256_bytes(canonical_json_bytes(members))[:16]


GENERATOR_VERSION = _generator_version()


class DigestAuthorityError(ResearchReportsError):
    """Correction or historical-cutoff authority is missing/inconsistent."""


def _item(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "source_event_ref": event["event_id"],
        "source_record_ref": event["source_record_ref"],
        "artifact_or_ledger_refs": event["subject_refs"],
        "occurred_at": event["occurred_at"],
        "observed_at": event["observed_at"],
        "status_axis": event["status_axis"],
        "status": event["to_state"],
        "verification_status": event["verification_status"],
        "evidence_refs": event["evidence_refs"],
        "text": event["display_text"],
    }


def _sections(kind: str, events: list[dict]) -> list[dict]:
    buckets = {title: [] for title in SECTION_TITLES[kind]}
    for event in events:
        item = _item(event)
        if kind == "daily":
            categories = [event["daily_category"]]
        elif kind == "weekly":
            categories = list(event["weekly_categories"])
            if event.get("semantic_authority") == "RESEARCH_OPPORTUNITIES_TENSION_AUTHORITY":
                categories.append("明确来源的潜在张力")
            if (
                event.get("source_system") == "ARTIFACT_REGISTRY"
                and set(event.get("explicit_tags", []))
                & {"important", "priority:high", "severity:high"}
            ):
                categories.append("明确标记的重要变化")
        else:
            categories = list(event["monthly_categories"])
            if event.get("semantic_authority") == "DECISION_LOG_DIRECTION_DECISION_AUTHORITY":
                categories.append("明确记录的研究方向变化")
            if (
                event.get("source_system") == "ARTIFACT_REGISTRY"
                and "long_term_todo" in set(event.get("explicit_tags", []))
            ):
                categories.append("长期待办")
        for category in dict.fromkeys(categories):
            if category in buckets:
                buckets[category].append(deepcopy(item))
    return [
        {
            "section_id": f"S{index:02d}",
            "title": title,
            "items": buckets[title],
        }
        for index, title in enumerate(SECTION_TITLES[kind], start=1)
    ]


def _not_assessed(kind: str, issues: list[dict], sections: list[dict]) -> list[dict]:
    values = [
        {
            "dimension": "semantic_priority_quality",
            "status": "NOT_ASSESSED",
            "reason_code": "NO_SEMANTIC_PRIORITY_AUTHORITY_IN_DataContracts_INPUT",
        }
    ]
    section_items = {section["title"]: section["items"] for section in sections}
    if kind == "weekly" and not section_items["明确来源的潜在张力"]:
        values.append(
            {
                "dimension": "potential_tensions",
                "status": "NOT_ASSESSED",
                "reason_code": "RESEARCH_OPPORTUNITIES_AUTHORITY_EVENT_NOT_AVAILABLE",
            }
        )
    if kind == "monthly" and not section_items["明确记录的研究方向变化"]:
        values.append(
            {
                "dimension": "research_direction_changes",
                "status": "NOT_ASSESSED",
                "reason_code": "DECISION_LOG_DECISION_AUTHORITY_EVENT_NOT_AVAILABLE",
            }
        )
    if any(issue["blocking_status"] == "non_blocking" for issue in issues):
        values.append(
            {
                "dimension": "optional_source_coverage",
                "status": "NOT_ASSESSED",
                "reason_code": "OPTIONAL_SOURCE_RECORD_UNPARSEABLE_OR_MISSING",
            }
        )
    if any(issue["error_code"] == "LATE_AUTHORITY_MISSING_OR_MISMATCH" for issue in issues):
        values.append(
            {
                "dimension": "late_event_coverage",
                "status": "NOT_ASSESSED",
                "reason_code": "ORIGINAL_CUTOFF_OR_DIGEST_AUTHORITY_UNRESOLVED",
            }
        )
    return values


def _common_change_digest(
    *,
    digest_id: str,
    parent_refs: list[str],
    evidence_refs: list[str],
    kind: str,
    revision: int,
    supersedes_digest_id: str | None,
) -> dict[str, Any]:
    payload = {
        "object_id": digest_id,
        "schema_version": "2.0",
        "revision": revision,
        "producer": "RESEARCH_REPORTS",
        "consumers": ["Retrieval", "Analysis"],
        "state": "draft" if revision == 1 else "validated",
        "parent_refs": parent_refs,
        "provenance_refs": evidence_refs,
        "supersedes": supersedes_digest_id,
        "immutable_fields": IMMUTABLE_FIELDS,
        "failure_semantics": FAILURE_SEMANTICS,
        "change_scope": ["KNOWLEDGE_ADMISSION", "RUNTIME_LOG", "ARTIFACT_REGISTRY"],
        "summary": f"Deterministic RESEARCH_REPORTS {kind} change log; domain details are source-backed.",
        "evidence_refs": evidence_refs,
    }
    payload["content_hash"] = typed_payload_hash(payload)
    return {"object_type": "ChangeDigest", "payload": payload}


def _validate_prior(
    *,
    digest_kind: str,
    revision: int,
    supersedes_digest_id: str | None,
    prior_digest: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise DigestAuthorityError("revision must be a positive integer")
    if revision == 1:
        if supersedes_digest_id is not None or prior_digest is not None:
            raise DigestAuthorityError("base digest must not have prior/supersedes authority")
        return None, None
    if not isinstance(prior_digest, dict):
        raise DigestAuthorityError("correction requires a frozen prior-digest authority object")
    required = {
        "digest_id",
        "digest_kind",
        "revision",
        "raw_sha256",
        "common_payload_hash",
        "common_object",
    }
    if set(prior_digest) != required:
        raise DigestAuthorityError("prior-digest authority fields mismatch")
    prior_id = prior_digest["digest_id"]
    if prior_digest["digest_kind"] != digest_kind:
        raise DigestAuthorityError("correction digest kind differs from frozen predecessor")
    if not isinstance(prior_id, str) or not prior_id or supersedes_digest_id != prior_id:
        raise DigestAuthorityError("supersedes ID is absent or differs from frozen prior authority")
    prior_revision = prior_digest["revision"]
    if isinstance(prior_revision, bool) or not isinstance(prior_revision, int) or revision != prior_revision + 1:
        raise DigestAuthorityError("correction revision must be prior revision plus one")
    for field in ("raw_sha256", "common_payload_hash"):
        value = prior_digest[field]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789ABCDEF" for char in value):
            raise DigestAuthorityError(f"invalid prior {field}")
    common = prior_digest["common_object"]
    if not isinstance(common, dict) or common.get("object_type") != "ChangeDigest":
        raise DigestAuthorityError("prior common object is missing")
    payload = common.get("payload")
    if not isinstance(payload, dict) or payload.get("object_id") != prior_id or payload.get("revision") != prior_revision:
        raise DigestAuthorityError("prior common identity/revision mismatch")
    observed_common_hash = payload.get("content_hash", {}).get("value")
    if observed_common_hash != prior_digest["common_payload_hash"] or typed_payload_hash(payload).get("value") != observed_common_hash:
        raise DigestAuthorityError("prior common payload hash is stale")
    return prior_id, {
        "digest_id": prior_id,
        "revision": prior_revision,
        "raw_sha256": prior_digest["raw_sha256"],
        "common_payload_hash": prior_digest["common_payload_hash"],
    }


def _late_proof(
    event: dict[str, Any],
    *,
    kind: str,
    current_window_ref: str,
    historical_authority: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    original_ref = original_window_ref(
        kind, parse_datetime(event["occurred_at"], field_name="occurred_at")
    )
    proof = (historical_authority or {}).get(event["source_content_hash"])
    if not isinstance(proof, dict):
        return None, original_ref
    required = {
        "source_content_hash",
        "event_id",
        "occurred_at",
        "observed_at",
        "original_window_ref",
        "original_cutoff_at",
        "original_digest_id",
        "original_digest_raw_sha256",
        "original_common_payload_hash",
        "current_window_ref",
        "authority_ref",
    }
    if set(proof) != required:
        return None, original_ref
    exact = (
        proof["source_content_hash"] == event["source_content_hash"]
        and proof["event_id"] == event["event_id"]
        and proof["occurred_at"] == event["occurred_at"]
        and proof["observed_at"] == event["observed_at"]
        and proof["original_window_ref"] == original_ref
        and proof["current_window_ref"] == current_window_ref
    )
    try:
        original_cutoff = parse_datetime(proof["original_cutoff_at"], field_name="original_cutoff_at")
        observed = parse_datetime(event["observed_at"], field_name="observed_at")
    except (TypeError, ValueError):
        return None, original_ref
    hashes_valid = all(
        isinstance(proof[field], str)
        and len(proof[field]) == 64
        and all(char in "0123456789ABCDEF" for char in proof[field])
        for field in ("original_digest_raw_sha256", "original_common_payload_hash")
    )
    ids_valid = all(
        isinstance(proof[field], str) and proof[field]
        for field in ("original_digest_id", "authority_ref")
    )
    if not exact or not hashes_valid or not ids_valid:
        return None, original_ref
    if observed <= original_cutoff:
        # It was visible by the original cutoff; this is not a late arrival.
        return {}, original_ref
    return proof, original_ref


def build_digest(
    snapshot: dict[str, Any],
    *,
    digest_kind: str,
    window_start: str,
    window_end: str,
    cutoff_at: str,
    revision: int = 1,
    supersedes_digest_id: str | None = None,
    prior_digest: dict[str, Any] | None = None,
    historical_authority: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start, end, cutoff = validate_window(digest_kind, window_start, window_end, cutoff_at)
    prior_id, prior_binding = _validate_prior(
        digest_kind=digest_kind,
        revision=revision,
        supersedes_digest_id=supersedes_digest_id,
        prior_digest=prior_digest,
    )
    issues = deepcopy(snapshot["issues"])
    conflicts = deepcopy(snapshot.get("conflicts", []))
    window_ref = f"{digest_kind}:{start.isoformat()}..{end.isoformat()}"
    for issue in issues:
        issue["affected_window"] = window_ref
    current_events: list[dict] = []
    late_events: list[dict] = []
    late_arrivals: list[dict] = []
    for event in snapshot["events"]:
        occurred = parse_datetime(event["occurred_at"], field_name="occurred_at").astimezone(start.tzinfo)
        observed = parse_datetime(event["observed_at"], field_name="observed_at").astimezone(start.tzinfo)
        if observed > cutoff:
            continue
        if start <= occurred < end:
            current_events.append(event)
            continue
        if occurred < start and start <= observed < end:
            proof, original_ref = _late_proof(
                event,
                kind=digest_kind,
                current_window_ref=window_ref,
                historical_authority=historical_authority,
            )
            if proof is None:
                issue = make_issue(
                    error_code="LATE_AUTHORITY_MISSING_OR_MISMATCH",
                    source_record_ref=event["source_record_ref"],
                    source_content_hash=event["source_content_hash"],
                    observed_at=event["observed_at"],
                    blocking=True,
                    evidence_ref=event["event_id"],
                )
                issue["affected_window"] = window_ref
                issues.append(issue)
            elif proof:
                late_events.append(event)
                late = _item(event)
                late.update(
                    {
                        "original_window_ref": original_ref,
                        "original_cutoff_at": proof["original_cutoff_at"],
                        "original_digest_id": proof["original_digest_id"],
                        "original_digest_raw_sha256": proof["original_digest_raw_sha256"],
                        "original_common_payload_hash": proof["original_common_payload_hash"],
                        "late_authority_ref": proof["authority_ref"],
                    }
                )
                late_arrivals.append(late)
    current_events.sort(key=lambda value: (value["occurred_at"], value["event_kind"], value["event_id"]))
    late_events.sort(key=lambda value: (value["occurred_at"], value["event_kind"], value["event_id"]))
    late_arrivals.sort(key=lambda value: (value["occurred_at"], value["event_id"]))
    issues.sort(key=lambda value: value["issue_id"])
    sections = _sections(digest_kind, current_events)
    blocking = any(issue["blocking_status"] == "blocking" for issue in issues) or bool(conflicts)
    report_status = "blocked_incomplete" if blocking else "complete"
    identity = {
        "schema_version": "2.0",
        "domain_contract_version": "1.1",
        "digest_kind": digest_kind,
        "report_timezone": REPORT_TIMEZONE,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "input_manifest_sha256": snapshot["manifest_sha256"],
        "generator_version": GENERATOR_VERSION,
        "revision": revision,
        "prior_binding": prior_binding,
    }
    digest_id = "CD-" + sha256_bytes(canonical_json_bytes(identity))[:32]
    if digest_id == prior_id:
        raise DigestAuthorityError("correction identity must differ from predecessor")
    admitted_for_lineage = current_events + late_events
    event_ids = sorted({event["event_id"] for event in admitted_for_lineage})
    source_manifest_anchor = snapshot.get(
        "source_manifest_anchor", "SOURCE-MANIFEST-" + snapshot["manifest_sha256"][:24]
    )
    parent_refs = event_ids or [source_manifest_anchor]
    evidence_refs = sorted(set(event_ids) | {source_manifest_anchor})
    report_payload = {
        "object_type": "ChangeDigest",
        "schema_version": "2.0",
        "domain_contract_version": "1.1",
        "digest_id": digest_id,
        "revision": revision,
        "digest_kind": digest_kind,
        "report_timezone": REPORT_TIMEZONE,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "input_manifest_sha256": snapshot["manifest_sha256"],
        "generator_version": GENERATOR_VERSION,
        "source_snapshot_refs": snapshot["members"],
        "report_status": report_status,
        "authority_status": "shadow_non_authoritative",
        "sections": sections,
        "late_arrivals": late_arrivals,
        "conflicts": conflicts,
        "unparseable_events": issues,
        "not_assessed_dimensions": _not_assessed(digest_kind, issues, sections),
        "supersedes_digest_id": prior_id,
        "supersedes_binding": prior_binding,
        "generated_at": cutoff.isoformat(),
        "external_calls": 0,
        "external_tokens": 0,
        "cost_cny": 0,
        "production_mutations": 0,
        "production_activation": False,
    }
    report_payload_hash = sha256_bytes(canonical_json_bytes(report_payload))
    digest = {
        **report_payload,
        "report_payload_hash": {
            "algorithm": "sha256",
            "hash_kind": "rfc8785_jcs_sha256",
            "scope": "REPORT_PAYLOAD",
            "value": report_payload_hash,
        },
        "common_object": _common_change_digest(
            digest_id=digest_id,
            parent_refs=parent_refs,
            evidence_refs=evidence_refs,
            kind=digest_kind,
            revision=revision,
            supersedes_digest_id=prior_id,
        ),
    }
    digest["content_hash"] = typed_payload_hash(digest)
    return digest
