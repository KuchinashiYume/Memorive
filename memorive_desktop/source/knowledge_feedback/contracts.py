"""Exact Verification contracts for source snapshots and DerivedKnowledgeCandidate.

The Initialization common object is preserved byte-semantically as the nested
``common_object``.  Verification domain fields live beside that object so the accepted
Initialization schema is not silently widened.  All builders are deterministic and make
no filesystem, Registry, index, network, or production-state writes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .canonical import make_hashed_payload, typed_payload_hash, validate_hash_descriptor, verify_hashed_payload
from .errors import ContractRejected, SourceIneligible


COMMON_FAILURE_SEMANTICS = {
    "BLOCKED": "fail_closed",
    "ERROR": "quarantined",
    "FAIL": "fail_closed",
    "NOT_ASSESSED": "no_eligibility",
}
COMMON_IMMUTABLE_FIELDS = [
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "parent_refs",
    "provenance_refs",
    "supersedes",
]
PRODUCER = "KNOWLEDGE_FEEDBACK_KNOWLEDGE_FEEDBACK_CONTROLLED_KNOWLEDGE_FEEDBACK_V2"
CONSUMERS = ["M08", "KNOWLEDGE_FEEDBACK", "DECISION_LOG", "ARTIFACT_REGISTRY", "MODEL_EVALUATION"]
DKC_STATES = {
    "candidate",
    "review_pending",
    "approved_for_controlled_ingest",
    "rejected",
}
LEGAL_DKC_TRANSITIONS = {
    "candidate": {"review_pending", "rejected"},
    "review_pending": {"approved_for_controlled_ingest", "rejected"},
    "approved_for_controlled_ingest": set(),
    "rejected": set(),
}
SOURCE_KINDS = {
    "SYNTHETIC_ORIGINAL",
    "SYNTHETIC_VERIFIED_ANALYSIS",
    "SYNTHETIC_OPPORTUNITY_CANDIDATE",
    "SYNTHETIC_DERIVED",
}

_COMMON_KEYS = {
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "consumers",
    "state",
    "parent_refs",
    "provenance_refs",
    "supersedes",
    "immutable_fields",
    "failure_semantics",
    "derived_penalty",
    "human_approval_required",
    "human_approval",
    "controlled_ingest_authorized",
}
_CANDIDATE_KEYS = {
    "schema_version",
    "common_object",
    "candidate_kind",
    "reusable_claim",
    "reusable_claim_hash",
    "reusable_claim_hash_basis",
    "scope",
    "limitations",
    "source_snapshot_refs",
    "source_snapshot_hashes",
    "evidence_refs",
    "field_qualification_refs",
    "source_usage_bindings",
    "generation_basis",
    "producer_version",
    "review_state",
    "decision_receipt_ref",
    "derived_penalty_record_ref",
    "derived_penalty_record_hash",
    "derived_penalty_eligibility_ref",
    "self_reinforcement_audit_ref",
    "not_literature_card",
    "scientific_judgment_status",
    "test_only",
    "content_hash",
}
_SOURCE_SNAPSHOT_KEYS = {
    "schema_version",
    "snapshot_id",
    "frozen_at",
    "source_mode",
    "source_items",
    "reference_pack_status",
    "upstream_authorities",
    "real_source_count",
    "synthetic_fixture",
    "content_hash",
}
_SOURCE_ITEM_KEYS = {
    "source_ref",
    "source_kind",
    "revision",
    "content_hash",
    "review_status",
    "verification_result",
    "source_locator",
    "field_anchors",
    "verification_evidence_refs",
    "original_source_refs",
    "derived_only",
    "eligible",
    "issue_codes",
    "omission_boundary",
}
_SOURCE_OMISSION_BOUNDARY_KEYS = {
    "schema_version",
    "applicability",
    "completion_status",
    "tombstones",
    "plaintext_removed_values_present",
    "omission_manifest_hash_basis",
    "omission_manifest_hash",
}
_SOURCE_OMISSION_TOMBSTONE_KEYS = {
    "tombstone_ref",
    "target_ref",
    "removed_value_hash",
    "removed_value_hash_basis",
    "attempts",
    "initial_report_ref",
    "final_report_ref",
}
_SOURCE_OMISSION_ATTEMPT_KEYS = {
    "sonnet_initial_review_calls",
    "deepseek_repair_calls",
    "sonnet_targeted_review_calls",
}
_SOURCE_USAGE_BINDING_KEYS = {"source_ref", "used_target_refs"}
_HUMAN_DECISION_KEYS = {
    "decision_id",
    "decided_by",
    "decided_at",
    "decision",
    "evidence_refs",
}


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractRejected(f"{field} must be an object")
    return deepcopy(dict(value))


def _exact(value: Mapping[str, Any], keys: set[str], field: str) -> None:
    observed = set(value)
    if observed != keys:
        missing = sorted(keys - observed)
        extra = sorted(observed - keys)
        raise ContractRejected(f"{field} exact keys mismatch; missing={missing}, extra={extra}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractRejected(f"{field} must be non-empty exact text")
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ContractRejected(f"{field} must be an explicit boolean")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractRejected(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractRejected(f"{field} must be a non-negative integer")
    return value


def _finite_non_negative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractRejected(f"{field} must be numeric")
    number = float(value)
    if number < 0.0 or number != number or number in {float("inf"), float("-inf")}:
        raise ContractRejected(f"{field} must be finite and non-negative")
    return number


def _refs(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractRejected(f"{field} must be an array")
    result = [_text(item, f"{field}[]") for item in value]
    if not allow_empty and not result:
        raise ContractRejected(f"{field} must not be empty")
    if result != sorted(set(result)):
        raise ContractRejected(f"{field} must be sorted and unique")
    return result


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractRejected(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractRejected(f"{field} must include an offset")
    return text


def knowledge_value_hash(value: Any) -> dict[str, str]:
    """Hash a JSON-domain knowledge value without carrying it into Verification artifacts."""

    return typed_payload_hash({"knowledge_value": deepcopy(value)})


def knowledge_text_hash(value: str) -> dict[str, str]:
    """Hash a validated candidate claim in the shared knowledge-value domain."""

    return knowledge_value_hash(_text(value, "knowledge_text"))


def _omission_manifest_payload(boundary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": deepcopy(boundary["schema_version"]),
        "applicability": deepcopy(boundary["applicability"]),
        "completion_status": deepcopy(boundary["completion_status"]),
        "tombstones": deepcopy(boundary["tombstones"]),
        "plaintext_removed_values_present": deepcopy(
            boundary["plaintext_removed_values_present"]
        ),
        "omission_manifest_hash_basis": deepcopy(
            boundary["omission_manifest_hash_basis"]
        ),
    }


def validate_source_omission_boundary(
    value: Mapping[str, Any], *, field: str = "source_omission_boundary"
) -> dict[str, Any]:
    result = _mapping(value, field)
    _exact(result, _SOURCE_OMISSION_BOUNDARY_KEYS, field)
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_SOURCE_OMISSION_BOUNDARY_V1":
        raise SourceIneligible("SOURCE_OMISSION_BOUNDARY_SCHEMA_UNSUPPORTED")
    applicability = result["applicability"]
    if applicability not in {
        "NOT_APPLICABLE",
        "DIRECT_CARD_V6",
        "INHERITED_CARD_V6",
    }:
        raise SourceIneligible("SOURCE_OMISSION_APPLICABILITY_INVALID")
    completion = result["completion_status"]
    if completion not in {"NOT_APPLICABLE", "complete", "passed_with_omissions"}:
        raise SourceIneligible("SOURCE_OMISSION_COMPLETION_STATUS_INVALID")
    if result["plaintext_removed_values_present"] is not False:
        raise SourceIneligible("SOURCE_OMISSION_PLAINTEXT_REMOVED_VALUE_FORBIDDEN")
    if (
        result["omission_manifest_hash_basis"]
        != "JCS_SOURCE_OMISSION_BOUNDARY_EXCLUDING_HASH_V1"
    ):
        raise SourceIneligible("SOURCE_OMISSION_MANIFEST_HASH_BASIS_INVALID")
    tombstones = result["tombstones"]
    if isinstance(tombstones, (str, bytes)) or not isinstance(tombstones, Sequence):
        raise ContractRejected(f"{field}.tombstones must be an array")
    checked_tombstones: list[dict[str, Any]] = []
    tombstone_refs: list[str] = []
    target_refs: list[str] = []
    for ordinal, raw in enumerate(tombstones):
        tombstone_field = f"{field}.tombstones[{ordinal}]"
        tombstone = _mapping(raw, tombstone_field)
        _exact(tombstone, _SOURCE_OMISSION_TOMBSTONE_KEYS, tombstone_field)
        tombstone_ref = _text(tombstone["tombstone_ref"], f"{tombstone_field}.tombstone_ref")
        target_ref = _text(tombstone["target_ref"], f"{tombstone_field}.target_ref")
        if not tombstone_ref.startswith("fixture://") or not target_ref.startswith("fixture://"):
            raise SourceIneligible("SOURCE_OMISSION_SYNTHETIC_REF_MUST_BE_FIXTURE_ONLY")
        tombstone_refs.append(tombstone_ref)
        target_refs.append(target_ref)
        validate_hash_descriptor(
            tombstone["removed_value_hash"], f"{tombstone_field}.removed_value_hash"
        )
        if (
            tombstone["removed_value_hash_basis"]
            != "JCS_KNOWLEDGE_VALUE_WRAPPER_V1"
        ):
            raise SourceIneligible("SOURCE_OMISSION_REMOVED_VALUE_HASH_BASIS_INVALID")
        attempts = _mapping(tombstone["attempts"], f"{tombstone_field}.attempts")
        _exact(attempts, _SOURCE_OMISSION_ATTEMPT_KEYS, f"{tombstone_field}.attempts")
        for key in sorted(_SOURCE_OMISSION_ATTEMPT_KEYS):
            _non_negative_int(attempts[key], f"{tombstone_field}.attempts.{key}")
        if attempts["sonnet_initial_review_calls"] < 1:
            raise SourceIneligible("SOURCE_OMISSION_INITIAL_REVIEW_MISSING")
        initial_report = _text(
            tombstone["initial_report_ref"], f"{tombstone_field}.initial_report_ref"
        )
        final_report = tombstone["final_report_ref"]
        if final_report is not None:
            final_report = _text(final_report, f"{tombstone_field}.final_report_ref")
        if not initial_report.startswith("fixture://") or (
            final_report is not None and not final_report.startswith("fixture://")
        ):
            raise SourceIneligible("SOURCE_OMISSION_REPORT_REF_MUST_BE_FIXTURE_ONLY")
        checked_tombstones.append(tombstone)
    if tombstone_refs != sorted(tombstone_refs):
        raise SourceIneligible("SOURCE_OMISSION_TOMBSTONES_MUST_BE_SORTED")
    if len(tombstone_refs) != len(set(tombstone_refs)) or len(target_refs) != len(
        set(target_refs)
    ):
        raise SourceIneligible("SOURCE_OMISSION_TOMBSTONE_DUPLICATE")
    if applicability == "NOT_APPLICABLE":
        if completion != "NOT_APPLICABLE" or checked_tombstones:
            raise SourceIneligible("SOURCE_OMISSION_NOT_APPLICABLE_STATE_MISMATCH")
    else:
        if completion == "NOT_APPLICABLE":
            raise SourceIneligible("SOURCE_OMISSION_CARD_STATUS_MISSING")
        if (completion == "passed_with_omissions") != bool(checked_tombstones):
            raise SourceIneligible("SOURCE_OMISSION_STATUS_TOMBSTONE_MISMATCH")
    validate_hash_descriptor(result["omission_manifest_hash"], f"{field}.omission_manifest_hash")
    expected_hash = typed_payload_hash(_omission_manifest_payload(result))
    if result["omission_manifest_hash"] != expected_hash:
        raise SourceIneligible("SOURCE_OMISSION_MANIFEST_HASH_MISMATCH")
    return result


def make_source_omission_boundary(
    *,
    applicability: str,
    completion_status: str,
    tombstones: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    copied_tombstones = [deepcopy(dict(item)) for item in tombstones]
    copied_tombstones.sort(key=lambda item: str(item.get("tombstone_ref", "")))
    payload: dict[str, Any] = {
        "schema_version": "KNOWLEDGE_FEEDBACK_SOURCE_OMISSION_BOUNDARY_V1",
        "applicability": applicability,
        "completion_status": completion_status,
        "tombstones": copied_tombstones,
        "plaintext_removed_values_present": False,
        "omission_manifest_hash_basis": "JCS_SOURCE_OMISSION_BOUNDARY_EXCLUDING_HASH_V1",
    }
    payload["omission_manifest_hash"] = typed_payload_hash(
        _omission_manifest_payload(payload)
    )
    return validate_source_omission_boundary(payload)


def _validate_source_usage_bindings(
    value: Any, *, parent_refs: Sequence[str]
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ContractRejected("source_usage_bindings must be a non-empty array")
    checked: list[dict[str, Any]] = []
    refs: list[str] = []
    for ordinal, raw in enumerate(value):
        field = f"source_usage_bindings[{ordinal}]"
        binding = _mapping(raw, field)
        _exact(binding, _SOURCE_USAGE_BINDING_KEYS, field)
        source_ref = _text(binding["source_ref"], f"{field}.source_ref")
        _refs(binding["used_target_refs"], f"{field}.used_target_refs")
        refs.append(source_ref)
        checked.append(binding)
    if refs != sorted(set(refs)):
        raise ContractRejected("SOURCE_USAGE_BINDINGS_MUST_BE_SORTED_UNIQUE")
    if set(refs) != set(parent_refs):
        raise ContractRejected("SOURCE_USAGE_BINDINGS_PARENT_SET_MISMATCH")
    return checked


def _validate_human_decision(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    result = _mapping(value, field)
    _exact(result, _HUMAN_DECISION_KEYS, field)
    _text(result["decision_id"], f"{field}.decision_id")
    _text(result["decided_by"], f"{field}.decided_by")
    _timestamp(result["decided_at"], f"{field}.decided_at")
    if result["decision"] not in {"APPROVE", "REJECT"}:
        raise ContractRejected(f"{field}.decision is invalid")
    _refs(result["evidence_refs"], f"{field}.evidence_refs")
    return result


def candidate_version_ref(candidate: Mapping[str, Any]) -> str:
    checked = validate_derived_knowledge_candidate(candidate)
    common = checked["common_object"]
    return (
        f"{common['object_id']}@revision:{common['revision']}"
        f"#sha256:{checked['content_hash']['value']}"
    )


def validate_source_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    result = verify_hashed_payload(_mapping(value, "source_snapshot"), "source_snapshot")
    _exact(result, _SOURCE_SNAPSHOT_KEYS, "source_snapshot")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_KNOWLEDGE_SOURCE_SNAPSHOT_V2":
        raise ContractRejected("SOURCE_SNAPSHOT_SCHEMA_UNSUPPORTED")
    _text(result["snapshot_id"], "source_snapshot.snapshot_id")
    _timestamp(result["frozen_at"], "source_snapshot.frozen_at")
    if result["source_mode"] != "PUBLIC_SAFE_FULLY_SYNTHETIC_ONLY":
        raise SourceIneligible("SOURCE_MODE_NOT_PUBLIC_SAFE_SYNTHETIC")
    if result["reference_pack_status"] != "ABSENT_FAIL_CLOSED":
        raise SourceIneligible("Intake_REFERENCE_PACK_STATUS_MUST_REMAIN_ABSENT")
    if (
        isinstance(result["real_source_count"], bool)
        or not isinstance(result["real_source_count"], int)
        or result["real_source_count"] != 0
    ):
        raise SourceIneligible("REAL_SOURCE_COUNT_MUST_BE_ZERO")
    if _boolean(result["synthetic_fixture"], "source_snapshot.synthetic_fixture") is not True:
        raise SourceIneligible("SOURCE_SNAPSHOT_MUST_BE_EXPLICIT_SYNTHETIC_FIXTURE")
    upstream = _mapping(result["upstream_authorities"], "source_snapshot.upstream_authorities")
    if set(upstream) != {"initialization_common_object", "configuration_handoff", "intake_handoff"}:
        raise ContractRejected("SOURCE_UPSTREAM_AUTHORITY_EXACT_SET_MISMATCH")
    for key, descriptor in upstream.items():
        validate_hash_descriptor(descriptor, f"source_snapshot.upstream_authorities.{key}")
    items = result["source_items"]
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence) or not items:
        raise SourceIneligible("SOURCE_ITEMS_MUST_BE_NON_EMPTY")
    seen: set[str] = set()
    observed_refs: list[str] = []
    checked_items: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(items):
        item = _mapping(raw, f"source_items[{ordinal}]")
        _exact(item, _SOURCE_ITEM_KEYS, f"source_items[{ordinal}]")
        ref = _text(item["source_ref"], f"source_items[{ordinal}].source_ref")
        if ref in seen:
            raise SourceIneligible("DUPLICATE_SOURCE_REF")
        seen.add(ref)
        observed_refs.append(ref)
        if item["source_kind"] not in SOURCE_KINDS:
            raise SourceIneligible("SOURCE_KIND_UNSUPPORTED")
        _positive_int(item["revision"], f"source_items[{ordinal}].revision")
        validate_hash_descriptor(item["content_hash"], f"source_items[{ordinal}].content_hash")
        if item["review_status"] not in {"active", "accepted", "validated", "synthetic_control"}:
            raise SourceIneligible("SOURCE_REVIEW_STATUS_INELIGIBLE")
        if item["verification_result"] != "PASS":
            raise SourceIneligible("SOURCE_VERIFICATION_NOT_PASS")
        locator = _text(item["source_locator"], f"source_items[{ordinal}].source_locator")
        if not locator.startswith("fixture://"):
            raise SourceIneligible("SYNTHETIC_SOURCE_LOCATOR_MUST_BE_FIXTURE_ONLY")
        _refs(item["field_anchors"], f"source_items[{ordinal}].field_anchors")
        evidence_refs = _refs(
            item["verification_evidence_refs"],
            f"source_items[{ordinal}].verification_evidence_refs",
        )
        if not all(ref.startswith("fixture://") for ref in evidence_refs):
            raise SourceIneligible("SYNTHETIC_VERIFICATION_EVIDENCE_MUST_BE_FIXTURE_ONLY")
        _refs(
            item["original_source_refs"],
            f"source_items[{ordinal}].original_source_refs",
            allow_empty=item["source_kind"] == "SYNTHETIC_ORIGINAL",
        )
        derived_only = _boolean(item["derived_only"], f"source_items[{ordinal}].derived_only")
        eligible = _boolean(item["eligible"], f"source_items[{ordinal}].eligible")
        issues = _refs(
            item["issue_codes"],
            f"source_items[{ordinal}].issue_codes",
            allow_empty=True,
        )
        validate_source_omission_boundary(
            item["omission_boundary"],
            field=f"source_items[{ordinal}].omission_boundary",
        )
        if item["source_kind"] == "SYNTHETIC_ORIGINAL" and derived_only:
            raise SourceIneligible("ORIGINAL_SOURCE_CANNOT_BE_DERIVED_ONLY")
        if item["source_kind"] == "SYNTHETIC_DERIVED" and not derived_only:
            raise SourceIneligible("DERIVED_SOURCE_MUST_BE_MARKED_DERIVED_ONLY")
        if eligible and issues:
            raise SourceIneligible("ELIGIBLE_SOURCE_CANNOT_HAVE_ISSUES")
        if not eligible and not issues:
            raise SourceIneligible("INELIGIBLE_SOURCE_REQUIRES_ISSUE")
        checked_items.append(item)
    if observed_refs != sorted(observed_refs):
        raise SourceIneligible("SOURCE_ITEMS_MUST_BE_SORTED_BY_REF")
    eligible_items = [item for item in checked_items if item["eligible"]]
    if not eligible_items:
        raise SourceIneligible("NO_ELIGIBLE_SOURCE")
    if all(item["derived_only"] for item in eligible_items):
        raise SourceIneligible("DERIVED_ONLY_SOURCE_SET_BLOCKED")
    return result


def make_source_snapshot(
    *,
    snapshot_id: str,
    source_items: Sequence[Mapping[str, Any]],
    upstream_authorities: Mapping[str, Any],
    frozen_at: str,
) -> dict[str, Any]:
    copied_items = [deepcopy(dict(item)) for item in source_items]
    copied_items.sort(key=lambda item: str(item.get("source_ref", "")))
    return validate_source_snapshot(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_KNOWLEDGE_SOURCE_SNAPSHOT_V2",
                "snapshot_id": snapshot_id,
                "frozen_at": frozen_at,
                "source_mode": "PUBLIC_SAFE_FULLY_SYNTHETIC_ONLY",
                "source_items": copied_items,
                "reference_pack_status": "ABSENT_FAIL_CLOSED",
                "upstream_authorities": deepcopy(dict(upstream_authorities)),
                "real_source_count": 0,
                "synthetic_fixture": True,
            }
        )
    )


def _validate_common_object(value: Any) -> dict[str, Any]:
    result = verify_hashed_payload(_mapping(value, "common_object"), "common_object")
    _exact(result, _COMMON_KEYS, "common_object")
    _text(result["object_id"], "common_object.object_id")
    if result["schema_version"] != "2.0":
        raise ContractRejected("COMMON_OBJECT_SCHEMA_MUST_REMAIN_2_0")
    revision = _positive_int(result["revision"], "common_object.revision")
    if result["producer"] != PRODUCER or result["consumers"] != CONSUMERS:
        raise ContractRejected("COMMON_OBJECT_PRODUCER_OR_CONSUMERS_DRIFT")
    if result["state"] not in DKC_STATES:
        raise ContractRejected("DKC_STATE_INVALID")
    _refs(result["parent_refs"], "common_object.parent_refs")
    _refs(result["provenance_refs"], "common_object.provenance_refs")
    supersedes = result["supersedes"]
    if revision == 1 and supersedes is not None:
        raise ContractRejected("REVISION_ONE_SUPERSEDES_MUST_BE_NULL")
    if revision > 1:
        _text(supersedes, "common_object.supersedes")
        if supersedes == result["object_id"]:
            raise ContractRejected("SUPERSEDES_MUST_NOT_EQUAL_OBJECT_ID")
    if result["immutable_fields"] != COMMON_IMMUTABLE_FIELDS:
        raise ContractRejected("COMMON_OBJECT_IMMUTABLE_FIELDS_DRIFT")
    if result["failure_semantics"] != COMMON_FAILURE_SEMANTICS:
        raise ContractRejected("COMMON_OBJECT_FAILURE_SEMANTICS_DRIFT")
    _finite_non_negative(result["derived_penalty"], "common_object.derived_penalty")
    if result["human_approval_required"] is not True:
        raise ContractRejected("HUMAN_APPROVAL_REQUIRED_MUST_BE_TRUE")
    decision = _validate_human_decision(result["human_approval"], "common_object.human_approval")
    authorized = _boolean(
        result["controlled_ingest_authorized"],
        "common_object.controlled_ingest_authorized",
    )
    if result["state"] == "approved_for_controlled_ingest":
        if decision is None or decision["decision"] != "APPROVE" or not authorized:
            raise ContractRejected("APPROVED_STATE_REQUIRES_APPROVE_AND_OBJECT_AUTHORIZATION")
    elif authorized:
        raise ContractRejected("NON_APPROVED_STATE_CANNOT_AUTHORIZE_CONTROLLED_INGEST")
    if result["state"] == "rejected" and decision is not None and decision["decision"] != "REJECT":
        raise ContractRejected("REJECTED_STATE_CANNOT_CARRY_APPROVE")
    return result


def validate_derived_knowledge_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(
            _mapping(value, "derived_candidate"), "derived_candidate"
        )
    except Exception as exc:
        raise ContractRejected("DERIVED_CANDIDATE_HASH_INVALID") from exc
    _exact(result, _CANDIDATE_KEYS, "derived_candidate")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_DERIVED_KNOWLEDGE_CANDIDATE_V2":
        raise ContractRejected("DERIVED_CANDIDATE_SCHEMA_UNSUPPORTED")
    common = _validate_common_object(result["common_object"])
    if result["candidate_kind"] not in {
        "SYNTHETIC_REUSABLE_METHOD_NOTE",
        "SYNTHETIC_REUSABLE_BOUNDARY_NOTE",
    }:
        raise ContractRejected("KNOWLEDGE_CANDIDATE_KIND_UNSUPPORTED")
    reusable_claim = _text(result["reusable_claim"], "reusable_claim")
    validate_hash_descriptor(result["reusable_claim_hash"], "reusable_claim_hash")
    if result["reusable_claim_hash_basis"] != "JCS_KNOWLEDGE_VALUE_WRAPPER_V1":
        raise ContractRejected("REUSABLE_CLAIM_HASH_BASIS_INVALID")
    if result["reusable_claim_hash"] != knowledge_text_hash(reusable_claim):
        raise ContractRejected("REUSABLE_CLAIM_HASH_MISMATCH")
    _refs(result["scope"], "scope")
    _refs(result["limitations"], "limitations")
    snapshot_refs = _refs(result["source_snapshot_refs"], "source_snapshot_refs")
    snapshot_hashes = _mapping(result["source_snapshot_hashes"], "source_snapshot_hashes")
    if set(snapshot_hashes) != set(snapshot_refs):
        raise ContractRejected("SOURCE_SNAPSHOT_HASH_REF_SET_MISMATCH")
    for snapshot_ref, descriptor in snapshot_hashes.items():
        validate_hash_descriptor(descriptor, f"source_snapshot_hashes.{snapshot_ref}")
    evidence_refs = _refs(result["evidence_refs"], "evidence_refs")
    if not set(common["provenance_refs"]) <= set(evidence_refs):
        raise ContractRejected("CANDIDATE_EVIDENCE_MUST_COVER_PROVENANCE")
    _refs(result["field_qualification_refs"], "field_qualification_refs")
    _validate_source_usage_bindings(
        result["source_usage_bindings"], parent_refs=common["parent_refs"]
    )
    _text(result["generation_basis"], "generation_basis")
    if result["producer_version"] != PRODUCER:
        raise ContractRejected("CANDIDATE_PRODUCER_VERSION_DRIFT")
    if result["review_state"] != common["state"]:
        raise ContractRejected("CANDIDATE_REVIEW_STATE_COMMON_STATE_DRIFT")
    decision_ref = result["decision_receipt_ref"]
    decision = common["human_approval"]
    if decision is None:
        if decision_ref is not None:
            raise ContractRejected("CANDIDATE_DECISION_REF_WITHOUT_DECISION")
    elif decision_ref != decision["decision_id"]:
        raise ContractRejected("CANDIDATE_DECISION_REF_MISMATCH")
    _text(result["derived_penalty_record_ref"], "derived_penalty_record_ref")
    validate_hash_descriptor(result["derived_penalty_record_hash"], "derived_penalty_record_hash")
    _text(result["derived_penalty_eligibility_ref"], "derived_penalty_eligibility_ref")
    _text(result["self_reinforcement_audit_ref"], "self_reinforcement_audit_ref")
    if result["not_literature_card"] is not True:
        raise ContractRejected("DERIVED_KNOWLEDGE_MUST_NOT_MASQUERADE_AS_CARD")
    if result["scientific_judgment_status"] != "NOT_ASSESSED":
        raise ContractRejected("SCIENTIFIC_JUDGMENT_MUST_REMAIN_NOT_ASSESSED")
    if _boolean(result["test_only"], "test_only") is not True:
        raise ContractRejected("CONSTRUCTION_A_CANDIDATE_MUST_REMAIN_TEST_ONLY")
    return result


def make_derived_knowledge_candidate(
    *,
    object_id: str,
    parent_refs: Sequence[str],
    provenance_refs: Sequence[str],
    candidate_kind: str,
    reusable_claim: str,
    scope: Sequence[str],
    limitations: Sequence[str],
    source_snapshot: Mapping[str, Any],
    evidence_refs: Sequence[str],
    field_qualification_refs: Sequence[str],
    source_usage_bindings: Sequence[Mapping[str, Any]],
    generation_basis: str,
    derived_penalty: float,
    derived_penalty_record_ref: str,
    derived_penalty_record_hash: Mapping[str, Any],
    derived_penalty_eligibility_ref: str,
    self_reinforcement_audit_ref: str,
) -> dict[str, Any]:
    snapshot = validate_source_snapshot(source_snapshot)
    common = make_hashed_payload(
        {
            "object_id": object_id,
            "schema_version": "2.0",
            "revision": 1,
            "producer": PRODUCER,
            "consumers": CONSUMERS,
            "state": "candidate",
            "parent_refs": list(parent_refs),
            "provenance_refs": list(provenance_refs),
            "supersedes": None,
            "immutable_fields": COMMON_IMMUTABLE_FIELDS,
            "failure_semantics": COMMON_FAILURE_SEMANTICS,
            "derived_penalty": derived_penalty,
            "human_approval_required": True,
            "human_approval": None,
            "controlled_ingest_authorized": False,
        }
    )
    return validate_derived_knowledge_candidate(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_DERIVED_KNOWLEDGE_CANDIDATE_V2",
                "common_object": common,
                "candidate_kind": candidate_kind,
                "reusable_claim": reusable_claim,
                "reusable_claim_hash": knowledge_text_hash(reusable_claim),
                "reusable_claim_hash_basis": "JCS_KNOWLEDGE_VALUE_WRAPPER_V1",
                "scope": list(scope),
                "limitations": list(limitations),
                "source_snapshot_refs": [snapshot["snapshot_id"]],
                "source_snapshot_hashes": {
                    snapshot["snapshot_id"]: deepcopy(snapshot["content_hash"])
                },
                "evidence_refs": list(evidence_refs),
                "field_qualification_refs": list(field_qualification_refs),
                "source_usage_bindings": sorted(
                    [deepcopy(dict(item)) for item in source_usage_bindings],
                    key=lambda item: str(item.get("source_ref", "")),
                ),
                "generation_basis": generation_basis,
                "producer_version": PRODUCER,
                "review_state": "candidate",
                "decision_receipt_ref": None,
                "derived_penalty_record_ref": derived_penalty_record_ref,
                "derived_penalty_record_hash": deepcopy(dict(derived_penalty_record_hash)),
                "derived_penalty_eligibility_ref": derived_penalty_eligibility_ref,
                "self_reinforcement_audit_ref": self_reinforcement_audit_ref,
                "not_literature_card": True,
                "scientific_judgment_status": "NOT_ASSESSED",
                "test_only": True,
            }
        )
    )


def project_candidate_transition(
    candidate: Mapping[str, Any],
    *,
    target_state: str,
    human_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = validate_derived_knowledge_candidate(candidate)
    common = current["common_object"]
    if target_state not in LEGAL_DKC_TRANSITIONS[common["state"]]:
        raise ContractRejected(
            f"ILLEGAL_DKC_TRANSITION:{common['state']}->{target_state}"
        )
    checked_decision = _validate_human_decision(human_decision, "human_decision")
    if target_state == "approved_for_controlled_ingest":
        if checked_decision is None or checked_decision["decision"] != "APPROVE":
            raise ContractRejected("APPROVED_TRANSITION_REQUIRES_APPROVE")
    if target_state == "rejected":
        if checked_decision is not None and checked_decision["decision"] != "REJECT":
            raise ContractRejected("REJECTED_TRANSITION_REQUIRES_REJECT_OR_NULL")
    next_common = deepcopy(common)
    next_common.pop("content_hash")
    next_common["revision"] = common["revision"] + 1
    next_common["state"] = target_state
    next_common["supersedes"] = candidate_version_ref(current)
    next_common["human_approval"] = checked_decision
    next_common["controlled_ingest_authorized"] = (
        target_state == "approved_for_controlled_ingest"
    )
    result = deepcopy(current)
    result.pop("content_hash")
    result["common_object"] = make_hashed_payload(next_common)
    result["review_state"] = target_state
    result["decision_receipt_ref"] = (
        None if checked_decision is None else checked_decision["decision_id"]
    )
    return validate_derived_knowledge_candidate(make_hashed_payload(result))


def assert_candidate_binds_snapshot(
    candidate: Mapping[str, Any], source_snapshot: Mapping[str, Any]
) -> None:
    checked_candidate = validate_derived_knowledge_candidate(candidate)
    checked_snapshot = validate_source_snapshot(source_snapshot)
    if checked_candidate["source_snapshot_refs"] != [checked_snapshot["snapshot_id"]]:
        raise SourceIneligible("CANDIDATE_SOURCE_SNAPSHOT_REF_MISMATCH")
    if checked_candidate["source_snapshot_hashes"].get(checked_snapshot["snapshot_id"]) != checked_snapshot["content_hash"]:
        raise SourceIneligible("CANDIDATE_SOURCE_SNAPSHOT_HASH_MISMATCH")
    parent_refs = set(checked_candidate["common_object"]["parent_refs"])
    eligible_refs = {
        item["source_ref"] for item in checked_snapshot["source_items"] if item["eligible"]
    }
    if not parent_refs <= eligible_refs:
        raise SourceIneligible("CANDIDATE_PARENT_NOT_ELIGIBLE_IN_SNAPSHOT")
    items_by_ref = {
        item["source_ref"]: item for item in checked_snapshot["source_items"]
    }
    usage_by_ref = {
        item["source_ref"]: item
        for item in checked_candidate["source_usage_bindings"]
    }
    for source_ref in sorted(parent_refs):
        source_item = items_by_ref[source_ref]
        usage = usage_by_ref[source_ref]
        boundary = source_item["omission_boundary"]
        tombstones = boundary["tombstones"]
        blocked_targets = {item["target_ref"] for item in tombstones}
        used_targets = set(usage["used_target_refs"])
        if used_targets & blocked_targets:
            raise SourceIneligible("CANDIDATE_USES_TOMBSTONED_SOURCE_TARGET")
        if not used_targets <= set(source_item["field_anchors"]):
            raise SourceIneligible("CANDIDATE_SOURCE_USAGE_TARGET_NOT_ANCHORED")
        if any(
            checked_candidate["reusable_claim_hash"] == item["removed_value_hash"]
            for item in tombstones
        ):
            raise SourceIneligible("CANDIDATE_RECALLS_TOMBSTONED_CONTENT_HASH")
    if not any(
        item["source_ref"] in parent_refs and not item["derived_only"]
        for item in checked_snapshot["source_items"]
    ):
        raise SourceIneligible("CANDIDATE_HAS_NO_NON_DERIVED_SOURCE")


def utc_now_string() -> str:
    """Return an explicit UTC timestamp for non-frozen interactive callers."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "COMMON_FAILURE_SEMANTICS",
    "COMMON_IMMUTABLE_FIELDS",
    "CONSUMERS",
    "DKC_STATES",
    "LEGAL_DKC_TRANSITIONS",
    "PRODUCER",
    "assert_candidate_binds_snapshot",
    "candidate_version_ref",
    "knowledge_text_hash",
    "knowledge_value_hash",
    "make_derived_knowledge_candidate",
    "make_source_omission_boundary",
    "make_source_snapshot",
    "project_candidate_transition",
    "typed_payload_hash",
    "utc_now_string",
    "validate_derived_knowledge_candidate",
    "validate_source_omission_boundary",
    "validate_source_snapshot",
]

