"""Memorive FACT-IDENTITY/EVIDENCE_EXTRACTION per-Card fact identity producer."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, MutableSequence, Sequence

from .fact_identity_evidence_extraction_contract_core import (
    ContractError,
    canonical_json,
    sha256_bytes,
    validate_artifact_ref,
)


MERGE_STATUSES = ("distinct", "merged", "merge_ambiguous")
_FACT_POLICY_FIELDS = {
    "artifact_type",
    "policy_version",
    "claim_normalization_rules",
    "evidence_signature_rules",
    "semantic_scope_rules",
    "numeric_and_unit_rules",
    "merge_candidate_thresholds",
    "supersedes",
    "created_at",
}


class FactIdentityError(ContractError):
    """Raised when a fact identity contract is incomplete or ambiguous."""


class CrossPaperMergeBlocked(FactIdentityError):
    """Raised when a caller attempts to merge identities across papers."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactIdentityError("INVALID_TEXT", f"{field} must be non-empty text")
    return value.strip()


def _text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise FactIdentityError("INVALID_TEXT_LIST", f"{field} must be a text list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise FactIdentityError(
            "INVALID_TEXT_LIST", f"{field} contains empty or non-text values"
        )
    if len(value) != len(set(value)):
        raise FactIdentityError("DUPLICATE_TEXT_LIST", f"{field} must be unique")
    return value


def _audit(
    events: MutableSequence[dict[str, Any]] | None,
    event: str,
    reason_code: str,
    **details: Any,
) -> None:
    if events is not None:
        events.append(
            {"event": event, "reason_code": reason_code, "details": details}
        )


def validate_fact_fingerprint_policy(
    policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(policy, Mapping) or set(policy) != _FACT_POLICY_FIELDS:
        raise FactIdentityError(
            "FACT_POLICY_FIELDS", "fact fingerprint policy fields must match manual"
        )
    if policy.get("artifact_type") != "fact_fingerprint_policy":
        raise FactIdentityError("FACT_POLICY_TYPE", "wrong artifact type")
    _text(policy.get("policy_version"), "policy_version")
    for field in (
        "claim_normalization_rules",
        "evidence_signature_rules",
        "semantic_scope_rules",
        "numeric_and_unit_rules",
    ):
        _text_list(policy.get(field), field)
    if not isinstance(policy.get("merge_candidate_thresholds"), Mapping):
        raise FactIdentityError(
            "FACT_POLICY_THRESHOLDS", "merge thresholds must be an object"
        )
    _text(policy.get("created_at"), "created_at")
    return policy


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).strip().casefold().split())
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    value_text = format(number.normalize(), "f")
    return "0" if value_text in {"-0", ""} else value_text


def assert_no_legacy_fact_id(value: Any) -> None:
    if isinstance(value, Mapping):
        if "fact_id" in value:
            raise FactIdentityError(
                "LEGACY_FACT_ID_WRITE_FORBIDDEN", "fact_id cannot be persisted"
            )
        for child in value.values():
            assert_no_legacy_fact_id(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_legacy_fact_id(child)


def _fact_fingerprint(fact: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    claim_type = _normalize(fact.get("claim_type"))
    value = _normalize(fact.get("normalized_value"))
    unit = _normalize(fact.get("unit"))
    condition = _normalize(fact.get("condition"))
    semantic_role = _normalize(fact.get("semantic_role"))
    occurrences = fact.get("occurrences", [])
    anchors = []
    if isinstance(occurrences, list):
        for occurrence in occurrences:
            if isinstance(occurrence, Mapping):
                anchors.extend(occurrence.get("source_anchor_refs", []))
    complete = all((claim_type, value, unit, semantic_role, anchors))
    fingerprint = {
        "normalized_claim_hash": sha256_bytes(claim_type.encode("utf-8")),
        "evidence_signature": sha256_bytes(canonical_json(anchors).encode("utf-8")),
        "semantic_scope_key": sha256_bytes(
            canonical_json(
                {"condition": condition, "semantic_role": semantic_role}
            ).encode("utf-8")
        ),
        "numeric_signature": sha256_bytes(
            canonical_json({"value": value, "unit": unit}).encode("utf-8")
        ),
    }
    return fingerprint, complete


def build_fact_identity_sidecar(
    *,
    card_artifact_ref: Mapping[str, Any],
    source_chunks_manifest_ref: Mapping[str, Any],
    paper_id: str,
    fingerprint_policy_payload: Mapping[str, Any],
    fingerprint_policy_ref: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_artifact_ref(card_artifact_ref)
    validate_artifact_ref(source_chunks_manifest_ref)
    validate_fact_fingerprint_policy(fingerprint_policy_payload)
    validate_artifact_ref(
        fingerprint_policy_ref,
        payload=fingerprint_policy_payload,
        artifact_type="fact_fingerprint_policy",
    )
    _text(paper_id, "paper_id")
    groups: dict[str, dict[str, Any]] = {}
    for fact in facts:
        if "fact_id" in fact:
            raise FactIdentityError(
                "LEGACY_FACT_ID_WRITE_FORBIDDEN",
                "legacy input must use the explicit reader adapter",
            )
        occurrences = fact.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise FactIdentityError(
                "FACT_OCCURRENCES_MISSING", "each fact needs occurrences"
            )
        normalized_occurrences = []
        for occurrence in occurrences:
            required = {"occurrence_id", "field_path", "source_anchor_refs"}
            if not isinstance(occurrence, Mapping) or set(occurrence) != required:
                raise FactIdentityError(
                    "FACT_OCCURRENCE_FIELDS", "occurrence field set must be exact"
                )
            _text(occurrence.get("occurrence_id"), "occurrence_id")
            _text(occurrence.get("field_path"), "field_path")
            if not isinstance(occurrence.get("source_anchor_refs"), list):
                raise FactIdentityError(
                    "FACT_OCCURRENCE_ANCHORS", "source_anchor_refs must be a list"
                )
            normalized_occurrences.append(dict(occurrence))
        fingerprint, complete = _fact_fingerprint(fact)
        fingerprint_key = sha256_bytes(
            canonical_json({"paper_id": paper_id, "fingerprint": fingerprint}).encode(
                "utf-8"
            )
        )
        if not complete:
            raise FactIdentityError(
                "FACT_IDENTITY_INPUT_INCOMPLETE",
                "new sidecar identity cannot be guessed from incomplete facts",
            )
        if fingerprint_key not in groups:
            groups[fingerprint_key] = {
                "fact_record_id": "fr_" + fingerprint_key[:32],
                "supersedes_fact_record_id": fact.get("supersedes_fact_record_id"),
                "fact_fingerprint": fingerprint,
                "occurrences": [],
                "merge_status": "distinct",
            }
        groups[fingerprint_key]["occurrences"].extend(normalized_occurrences)
        if len(groups[fingerprint_key]["occurrences"]) > 1:
            groups[fingerprint_key]["merge_status"] = "merged"
    records = list(groups.values())
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = record["fact_fingerprint"]["normalized_claim_hash"]
        by_claim.setdefault(key, []).append(record)
    for candidates in by_claim.values():
        if len(candidates) > 1:
            for record in candidates:
                record["merge_status"] = "merge_ambiguous"
    for record in records:
        seen: set[str] = set()
        deduplicated = []
        for occurrence in record["occurrences"]:
            key = canonical_json(occurrence)
            if key not in seen:
                seen.add(key)
                deduplicated.append(occurrence)
        record["occurrences"] = deduplicated
    result = {
        "schema_version": "fact-identity-sidecar-v1",
        "card_artifact_ref": dict(card_artifact_ref),
        "paper_id": paper_id,
        "fingerprint_policy_ref": dict(fingerprint_policy_ref),
        "facts": sorted(records, key=lambda item: item["fact_record_id"]),
    }
    assert_no_legacy_fact_id(result)
    return result


def adapt_legacy_facts(
    *, paper_id: str, legacy_facts: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    output = []
    for item in legacy_facts:
        legacy = _text(item.get("fact_id"), "legacy.fact_id")
        digest = sha256_bytes(f"{paper_id}\x1f{legacy}".encode("utf-8"))
        cleaned = {key: value for key, value in item.items() if key != "fact_id"}
        assert_no_legacy_fact_id(cleaned)
        output.append(
            {
                "fact_record_id": "fr_" + digest[:32],
                "identity_status": "identity_unknown",
                "legacy_source_key_hash": sha256_bytes(legacy.encode("utf-8")),
                "adapted_fields": cleaned,
            }
        )
    assert_no_legacy_fact_id(output)
    return output


def lookup_fact_identity(
    sidecar: Mapping[str, Any] | None, *, occurrence_id: str
) -> dict[str, Any]:
    if sidecar is None:
        return {
            "identity_status": "identity_unknown",
            "reason_code": "SIDECAR_MISSING",
            "fact_record_id": None,
        }
    assert_no_legacy_fact_id(sidecar)
    matches = []
    for fact in sidecar.get("facts", []):
        if any(
            occurrence.get("occurrence_id") == occurrence_id
            for occurrence in fact.get("occurrences", [])
        ):
            matches.append(fact)
    if len(matches) != 1:
        return {
            "identity_status": "identity_unknown",
            "reason_code": "OCCURRENCE_NOT_UNIQUE",
            "fact_record_id": None,
        }
    return {
        "identity_status": "resolved",
        "reason_code": None,
        "fact_record_id": matches[0]["fact_record_id"],
    }


def merge_fact_identity_sidecars(
    sidecars: Sequence[Mapping[str, Any]],
    *,
    audit_events: MutableSequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not sidecars:
        raise FactIdentityError("SIDECAR_MERGE_EMPTY", "sidecars are required")
    paper_ids = {item.get("paper_id") for item in sidecars}
    if len(paper_ids) != 1:
        _audit(
            audit_events,
            "fact_identity_merge_blocked",
            "CROSS_PAPER_MERGE_FORBIDDEN",
            paper_ids=sorted(str(value) for value in paper_ids),
        )
        raise CrossPaperMergeBlocked(
            "CROSS_PAPER_MERGE_FORBIDDEN", "identity cannot cross papers"
        )
    merged: dict[str, dict[str, Any]] = {}
    for sidecar in sidecars:
        assert_no_legacy_fact_id(sidecar)
        for fact in sidecar.get("facts", []):
            fact_id = fact["fact_record_id"]
            if fact_id not in merged:
                merged[fact_id] = json.loads(canonical_json(fact))
            else:
                merged[fact_id]["occurrences"].extend(fact["occurrences"])
                merged[fact_id]["merge_status"] = "merged"
    result = {
        "schema_version": "fact-identity-sidecar-v1",
        "card_artifact_ref": dict(sidecars[0]["card_artifact_ref"]),
        "paper_id": next(iter(paper_ids)),
        "fingerprint_policy_ref": dict(sidecars[0]["fingerprint_policy_ref"]),
        "facts": sorted(merged.values(), key=lambda item: item["fact_record_id"]),
    }
    assert_no_legacy_fact_id(result)
    return result


__all__ = [
    "CrossPaperMergeBlocked",
    "FactIdentityError",
    "MERGE_STATUSES",
    "adapt_legacy_facts",
    "assert_no_legacy_fact_id",
    "build_fact_identity_sidecar",
    "lookup_fact_identity",
    "merge_fact_identity_sidecars",
    "validate_fact_fingerprint_policy",
]
