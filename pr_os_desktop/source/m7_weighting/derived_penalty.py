"""Authority-only DerivedPenalty and fail-closed override contract."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import (
    make_hashed_payload,
    require_non_empty_string,
    require_utc_timestamp,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)
from .errors import ContractViolation
from .shadow_contracts import (
    reject_scorer_or_oracle_fields,
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_string_list,
    validate_derived_penalty_policy,
    validate_retrieval_policy_snapshot,
)


UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED = "UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED"
PROVENANCE_STATES = ("original", "derived", "unknown")

_RECORD_KEYS = {
    "candidate_id",
    "provenance_state",
    "derived_type",
    "parent_refs",
    "source_refs",
    "generation_basis_ref",
    "producer_version",
    "lineage_refs",
    "penalty_policy_version",
    "penalty_policy_hash",
    "design_prior",
    "base_penalty",
    "override",
    "effective_penalty",
    "authority_blocked",
    "issue_codes",
    "content_hash",
}
_OVERRIDE_KEYS = {
    "who",
    "when",
    "item",
    "old",
    "new",
    "reason",
    "event_locator",
    "event_hash",
}
_EVENT_LOCATOR_RE = re.compile(r"^(?:M08|M11|M12|M13):[^\r\n]+$")


def _nullable_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return require_non_empty_string(value, field_name)


def _validate_override(
    value: Any, *, candidate_id: str, base_penalty: float, effective_penalty: float
) -> dict[str, Any] | None:
    if value is None:
        if not math.isclose(base_penalty, effective_penalty, abs_tol=1e-12):
            raise ContractViolation("effective_penalty differs without a complete override")
        return None
    if not isinstance(value, Mapping):
        raise ContractViolation("derived_penalty_record.override must be null or an object")
    result = deepcopy(dict(value))
    require_exact_keys(result, _OVERRIDE_KEYS, "derived_penalty_record.override")
    require_non_empty_string(result["who"], "derived_penalty_record.override.who")
    require_utc_timestamp(result["when"], "derived_penalty_record.override.when")
    if require_non_empty_string(
        result["item"], "derived_penalty_record.override.item"
    ) != candidate_id:
        raise ContractViolation("override.item must equal candidate_id")
    old = require_finite_number(
        result["old"], "derived_penalty_record.override.old", minimum=0.0, maximum=1.0
    )
    new = require_finite_number(
        result["new"], "derived_penalty_record.override.new", minimum=0.0, maximum=1.0
    )
    if not math.isclose(old, base_penalty, abs_tol=1e-12):
        raise ContractViolation("override.old must equal base_penalty")
    if not math.isclose(new, effective_penalty, abs_tol=1e-12):
        raise ContractViolation("override.new must equal effective_penalty")
    require_non_empty_string(result["reason"], "derived_penalty_record.override.reason")
    locator = require_non_empty_string(
        result["event_locator"], "derived_penalty_record.override.event_locator"
    )
    if not _EVENT_LOCATOR_RE.fullmatch(locator):
        raise ContractViolation("override.event_locator must bind M08/M11/M12/M13 evidence")
    validate_hash_descriptor(result["event_hash"], "derived_penalty_record.override.event_hash")
    return result


def _validate_derived_penalty_record_structure(
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Private structural validation before authoritative policy binding."""

    if not isinstance(record, Mapping):
        raise ContractViolation("derived_penalty_record must be an object")
    result = deepcopy(dict(record))
    reject_scorer_or_oracle_fields(result, "derived_penalty_record")
    require_exact_keys(result, _RECORD_KEYS, "derived_penalty_record")
    candidate_id = require_non_empty_string(
        result["candidate_id"], "derived_penalty_record.candidate_id"
    )
    state = result["provenance_state"]
    if state not in PROVENANCE_STATES:
        raise ContractViolation("derived_penalty_record.provenance_state is invalid")
    derived_type = _nullable_string(result["derived_type"], "derived_penalty_record.derived_type")
    parent_refs = require_string_list(
        result["parent_refs"], "derived_penalty_record.parent_refs", allow_empty=True
    )
    source_refs = require_string_list(
        result["source_refs"], "derived_penalty_record.source_refs", allow_empty=True
    )
    generation_basis_ref = _nullable_string(
        result["generation_basis_ref"], "derived_penalty_record.generation_basis_ref"
    )
    producer_version = _nullable_string(
        result["producer_version"], "derived_penalty_record.producer_version"
    )
    lineage_refs = require_string_list(
        result["lineage_refs"], "derived_penalty_record.lineage_refs", allow_empty=True
    )
    require_non_empty_string(
        result["penalty_policy_version"], "derived_penalty_record.penalty_policy_version"
    )
    validate_hash_descriptor(
        result["penalty_policy_hash"], "derived_penalty_record.penalty_policy_hash"
    )
    design_prior = require_finite_number(
        result["design_prior"],
        "derived_penalty_record.design_prior",
        minimum=0.0,
        maximum=1.0,
    )
    base_penalty = require_finite_number(
        result["base_penalty"],
        "derived_penalty_record.base_penalty",
        minimum=0.0,
        maximum=1.0,
    )
    effective_penalty = require_finite_number(
        result["effective_penalty"],
        "derived_penalty_record.effective_penalty",
        minimum=0.0,
        maximum=1.0,
    )
    authority_blocked = require_boolean(
        result["authority_blocked"], "derived_penalty_record.authority_blocked"
    )
    issue_codes = require_string_list(
        result["issue_codes"], "derived_penalty_record.issue_codes", allow_empty=True
    )
    override = _validate_override(
        result["override"],
        candidate_id=candidate_id,
        base_penalty=base_penalty,
        effective_penalty=effective_penalty,
    )

    if state == "original":
        if derived_type is not None or generation_basis_ref is not None or producer_version is not None:
            raise ContractViolation("original provenance cannot carry derived-generation fields")
        if parent_refs or not source_refs or not lineage_refs:
            raise ContractViolation("original provenance requires source and lineage refs only")
        if not all(
            math.isclose(value, 1.0, abs_tol=1e-12)
            for value in (design_prior, base_penalty, effective_penalty)
        ):
            raise ContractViolation("complete original evidence must use penalty 1.0")
        if authority_blocked or issue_codes or override is not None:
            raise ContractViolation("complete original evidence cannot be blocked or overridden")
    elif state == "derived":
        if not derived_type or not parent_refs or not source_refs or not generation_basis_ref:
            raise ContractViolation("derived provenance requires type, parent, source and basis")
        if not producer_version or not lineage_refs:
            raise ContractViolation("derived provenance requires producer version and lineage")
        if authority_blocked:
            raise ContractViolation("complete derived provenance must not be authority-blocked")
        if issue_codes:
            raise ContractViolation("complete derived provenance cannot carry blocking issues")
        if not math.isclose(design_prior, base_penalty, abs_tol=1e-12):
            raise ContractViolation("base_penalty must equal the frozen design prior")
    else:
        if not authority_blocked:
            raise ContractViolation("unknown provenance must block authority")
        if not math.isclose(base_penalty, 0.0, abs_tol=1e-12) or not math.isclose(
            effective_penalty, 0.0, abs_tol=1e-12
        ):
            raise ContractViolation("unknown provenance authority penalty must be 0")
        if override is not None:
            raise ContractViolation("unknown provenance cannot be repaired by override")
        if issue_codes != [UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED]:
            raise ContractViolation("unknown provenance requires the frozen blocking issue")
        if not math.isclose(design_prior, 0.0, abs_tol=1e-12):
            raise ContractViolation("unknown provenance design_prior must remain 0")
    return verify_hashed_payload(result, "derived_penalty_record")


def validate_derived_penalty_record(
    record: Mapping[str, Any],
    *,
    penalty_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a record only when its frozen authoritative policy is supplied."""

    if penalty_policy is None:
        raise ContractViolation(
            "DerivedPenaltyRecord consumption requires the frozen penalty policy"
        )
    return validate_derived_penalty_against_policy(record, penalty_policy)


def validate_derived_penalty_against_policy(
    record: Mapping[str, Any], penalty_policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind a record to one exact frozen snapshot DerivedPenalty policy."""

    checked = _validate_derived_penalty_record_structure(record)
    policy = validate_derived_penalty_policy(penalty_policy)
    if checked["penalty_policy_version"] != policy["policy_version"]:
        raise ContractViolation("DerivedPenaltyRecord policy version differs from snapshot")
    expected_policy_hash = typed_payload_hash(policy)
    if checked["penalty_policy_hash"] != expected_policy_hash:
        raise ContractViolation("DerivedPenaltyRecord policy hash differs from snapshot")
    state = checked["provenance_state"]
    if state == "derived":
        derived_type = checked["derived_type"]
        if derived_type not in policy["type_penalties"]:
            raise ContractViolation("derived_type is absent from the frozen penalty table")
        expected = float(policy["type_penalties"][derived_type])
        if not math.isclose(float(checked["design_prior"]), expected, abs_tol=1e-12):
            raise ContractViolation("derived design_prior differs from frozen type penalty")
        if not math.isclose(float(checked["base_penalty"]), expected, abs_tol=1e-12):
            raise ContractViolation("derived base_penalty differs from frozen type penalty")
    elif state == "original":
        if not all(
            math.isclose(float(checked[name]), 1.0, abs_tol=1e-12)
            for name in ("design_prior", "base_penalty", "effective_penalty")
        ):
            raise ContractViolation("original penalty semantics drifted from 1.0")
    else:
        if not all(
            math.isclose(float(checked[name]), 0.0, abs_tol=1e-12)
            for name in ("design_prior", "base_penalty", "effective_penalty")
        ):
            raise ContractViolation("unknown penalty semantics drifted from authority block")
    override = checked["override"]
    if override is not None:
        allowlist = {
            item["event_locator"]: item["event_hash"]
            for item in policy["override_event_allowlist"]
        }
        if allowlist.get(override["event_locator"]) != override["event_hash"]:
            raise ContractViolation("override event locator/hash is not frozen in the allowlist")
    return checked


def validate_derived_penalty_against_snapshot(
    record: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate a record against the policy embedded in a valid retrieval snapshot."""

    checked_snapshot = validate_retrieval_policy_snapshot(snapshot)
    return validate_derived_penalty_against_policy(
        record, checked_snapshot["derived_penalty_policy"]
    )


def build_derived_penalty_record(
    *,
    candidate_id: str,
    provenance_state: str,
    derived_type: str | None,
    parent_refs: Sequence[str],
    source_refs: Sequence[str],
    generation_basis_ref: str | None,
    producer_version: str | None,
    lineage_refs: Sequence[str],
    penalty_policy: Mapping[str, Any],
    design_prior: float,
    base_penalty: float,
    override: Mapping[str, Any] | None,
    effective_penalty: float,
    authority_blocked: bool,
    issue_codes: Sequence[str],
) -> dict[str, Any]:
    """Build and hash one exact DerivedPenaltyRecord."""

    checked_policy = validate_derived_penalty_policy(penalty_policy)
    payload = {
        "candidate_id": candidate_id,
        "provenance_state": provenance_state,
        "derived_type": derived_type,
        "parent_refs": list(parent_refs),
        "source_refs": list(source_refs),
        "generation_basis_ref": generation_basis_ref,
        "producer_version": producer_version,
        "lineage_refs": list(lineage_refs),
        "penalty_policy_version": checked_policy["policy_version"],
        "penalty_policy_hash": typed_payload_hash(checked_policy),
        "design_prior": design_prior,
        "base_penalty": base_penalty,
        "override": deepcopy(dict(override)) if override is not None else None,
        "effective_penalty": effective_penalty,
        "authority_blocked": authority_blocked,
        "issue_codes": list(issue_codes),
    }
    return validate_derived_penalty_record(
        make_hashed_payload(payload), penalty_policy=checked_policy
    )


def apply_authority_penalty(
    *,
    relevance_subtotal: float,
    authority_subtotal: float,
    penalty_record: Mapping[str, Any],
    penalty_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply DerivedPenalty only to authority; relevance remains byte-for-byte numeric."""

    relevance = require_finite_number(
        relevance_subtotal, "relevance_subtotal", minimum=0.0, maximum=1.0
    )
    authority = require_finite_number(
        authority_subtotal, "authority_subtotal", minimum=0.0, maximum=1.0
    )
    penalty = validate_derived_penalty_against_policy(penalty_record, penalty_policy)
    effective = float(penalty["effective_penalty"])
    penalized_authority = 0.0 if penalty["authority_blocked"] else authority * effective
    return {
        "relevance_subtotal": relevance,
        "authority_subtotal": authority,
        "effective_penalty": effective,
        "authority_blocked": penalty["authority_blocked"],
        "penalized_authority_subtotal": penalized_authority,
        "combined_score": relevance + penalized_authority,
    }


__all__ = [
    "PROVENANCE_STATES",
    "UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED",
    "apply_authority_penalty",
    "build_derived_penalty_record",
    "validate_derived_penalty_against_policy",
    "validate_derived_penalty_against_snapshot",
    "validate_derived_penalty_record",
]
