"""Memorive FACT-IDENTITY/EVIDENCE_EXTRACTION required-slot assessment producer."""

from __future__ import annotations

from typing import Any, Mapping, MutableSequence, Sequence

from .normalize import quote_in_text
from .fact_identity_evidence_extraction_contract_core import (
    AbsenceEvidenceError,
    ContractError,
    _audit,
    _sha,
    _text,
    _text_list,
    now_iso,
    validate_absence_check_evidence,
    validate_artifact_ref,
    validate_review_status,
)


SLOT_STATES = (
    "present",
    "absent_in_source",
    "not_applicable",
    "extraction_gap",
    "not_assessed",
)

class TemplateBlockedError(ContractError):
    pass


class AnchorValidationError(ContractError):
    pass


_TEMPLATE_FIELDS = {
    "artifact_type",
    "template_id",
    "template_version",
    "literature_type",
    "required_slots",
    "applicability_rules",
    "absence_check_policy",
    "authority",
    "approval_status",
    "approval_event_ref",
    "approved_by",
    "approved_at",
    "supersedes",
    "fixture_only",
    "simulation",
    "non_authoritative",
    "required_sections_or_roles",
}


def validate_template_contract(
    template: Mapping[str, Any], *, mode: str
) -> Mapping[str, Any]:
    if not isinstance(template, Mapping):
        raise TemplateBlockedError("TEMPLATE_NOT_OBJECT", "template must be an object")
    missing = sorted(_TEMPLATE_FIELDS - set(template))
    if missing:
        raise TemplateBlockedError(
            "TEMPLATE_FIELDS_MISSING", f"missing fields: {missing}"
        )
    if template.get("artifact_type") != "required_slot_template_contract":
        raise TemplateBlockedError(
            "TEMPLATE_ARTIFACT_TYPE",
            "artifact_type must be required_slot_template_contract",
        )
    for field in ("template_id", "template_version", "literature_type"):
        _text(template.get(field), field)
    _text_list(template.get("applicability_rules"), "applicability_rules", allow_empty=True)
    absence_policy = template.get("absence_check_policy")
    absence_policy_fields = {
        "policy_id",
        "policy_version",
        "artifact_path",
        "sha256",
        "authority",
        "approval_status",
    }
    if not isinstance(absence_policy, Mapping) or set(absence_policy) != absence_policy_fields:
        raise TemplateBlockedError(
            "TEMPLATE_ABSENCE_POLICY_FIELDS",
            "absence_check_policy must match the formal embedded policy locator",
        )
    for field in ("policy_id", "policy_version", "artifact_path"):
        _text(absence_policy.get(field), f"absence_check_policy.{field}")
    _sha(absence_policy.get("sha256"), "absence_check_policy.sha256")
    if (
        absence_policy.get("authority") != "user-approved-contract"
        or absence_policy.get("approval_status") != "approved"
    ):
        raise TemplateBlockedError(
            "TEMPLATE_ABSENCE_POLICY_NOT_APPROVED",
            "absence_check_policy must identify the approved formal policy",
        )
    _text_list(
        template.get("required_sections_or_roles"), "required_sections_or_roles"
    )
    slots = template.get("required_slots")
    if not isinstance(slots, list) or not slots:
        raise TemplateBlockedError(
            "TEMPLATE_SLOTS_MISSING", "required_slots must be a non-empty list"
        )
    slot_ids: set[str] = set()
    for index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise TemplateBlockedError(
                "TEMPLATE_SLOT_INVALID", f"required_slots[{index}] must be an object"
            )
        required = {
            "slot_id",
            "slot_path",
            "assessment_required",
            "not_applicable_rule_ids",
        }
        if not required.issubset(slot):
            raise TemplateBlockedError(
                "TEMPLATE_SLOT_FIELDS", f"required_slots[{index}] fields are incomplete"
            )
        slot_id = _text(slot.get("slot_id"), f"required_slots[{index}].slot_id")
        _text(slot.get("slot_path"), f"required_slots[{index}].slot_path")
        if slot_id in slot_ids:
            raise TemplateBlockedError(
                "TEMPLATE_SLOT_DUPLICATE", f"duplicate slot_id {slot_id!r}"
            )
        slot_ids.add(slot_id)
        if slot.get("assessment_required") is not True:
            raise TemplateBlockedError(
                "TEMPLATE_SLOT_ASSESSMENT_REQUIRED",
                f"slot {slot_id!r} requires assessment_required=true",
            )
        _text_list(
            slot.get("not_applicable_rule_ids"),
            f"required_slots[{index}].not_applicable_rule_ids",
            allow_empty=True,
        )
    for field in ("fixture_only", "simulation", "non_authoritative"):
        if not isinstance(template.get(field), bool):
            raise TemplateBlockedError(
                "TEMPLATE_FLAG_INVALID", f"{field} must be boolean"
            )
    if mode == "test_mode":
        if not (
            template.get("authority") == "test-fixture"
            and template.get("approval_status") == "approved"
            and template.get("fixture_only") is True
            and template.get("simulation") is True
            and template.get("non_authoritative") is True
        ):
            raise TemplateBlockedError(
                "REQUIRED_SLOT_TEMPLATE_NOT_APPROVED",
                "test_mode accepts only approved non-authoritative test fixtures",
            )
    elif mode == "production_mode":
        if not (
            template.get("authority") == "user-approved-contract"
            and template.get("approval_status") == "approved"
            and template.get("fixture_only") is False
            and template.get("simulation") is False
            and template.get("non_authoritative") is False
            and isinstance(template.get("approval_event_ref"), str)
            and bool(template.get("approval_event_ref", "").strip())
            and isinstance(template.get("approved_by"), str)
            and bool(template.get("approved_by", "").strip())
            and isinstance(template.get("approved_at"), str)
            and bool(template.get("approved_at", "").strip())
        ):
            raise TemplateBlockedError(
                "REQUIRED_SLOT_TEMPLATE_NOT_APPROVED",
                "production_mode requires a user-approved non-fixture contract",
            )
    else:
        raise TemplateBlockedError("TEMPLATE_MODE_INVALID", f"unknown mode {mode!r}")
    return template


def validate_anchor_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(policy, Mapping) or set(policy) != {"anchor_normalization_policy"}:
        raise AnchorValidationError(
            "ANCHOR_POLICY_CONTAINER", "anchor_normalization_policy container is required"
        )
    value = policy["anchor_normalization_policy"]
    required = {
        "policy_version",
        "allowed_transforms",
        "forbidden_transforms",
        "quote_coverage_criterion",
        "mvp_scope",
        "legacy_backfill_policy",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AnchorValidationError(
            "ANCHOR_POLICY_FIELDS", "anchor policy field set must match the manual"
        )
    _text(value.get("policy_version"), "policy_version")
    allowed = _text_list(value.get("allowed_transforms"), "allowed_transforms")
    forbidden = _text_list(value.get("forbidden_transforms"), "forbidden_transforms")
    if not set(("unicode_width", "whitespace", "line_break")).issubset(allowed):
        raise AnchorValidationError(
            "ANCHOR_ALLOWED_TRANSFORMS", "required layout-only transforms are missing"
        )
    if not set(("lexical_substitution", "numeric_change", "unit_change")).issubset(
        forbidden
    ):
        raise AnchorValidationError(
            "ANCHOR_FORBIDDEN_TRANSFORMS", "semantic transforms must remain forbidden"
        )
    if value.get("quote_coverage_criterion") != "literal_substring_after_symmetric_normalization":
        raise AnchorValidationError(
            "ANCHOR_COVERAGE_CRITERION", "quote criterion must remain literal substring"
        )
    if value.get("legacy_backfill_policy") != "honest_missing_no_backfill":
        raise AnchorValidationError(
            "ANCHOR_BACKFILL_POLICY", "legacy anchors must not be fabricated"
        )
    return policy


def anchor_policy_version(policy: Mapping[str, Any]) -> str:
    validate_anchor_policy(policy)
    return str(policy["anchor_normalization_policy"]["policy_version"])


def _chunk_id(chunk: Mapping[str, Any]) -> str | None:
    value = chunk.get("chunk_id", chunk.get("id"))
    return value if isinstance(value, str) and value else None


def _chunk_text(chunk: Mapping[str, Any]) -> str | None:
    for key in ("text", "content", "chunk_text", "page_content"):
        value = chunk.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def validate_source_anchor(
    anchor: Mapping[str, Any],
    *,
    paper_id: str,
    source_content_hash: str,
    chunks: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> Mapping[str, Any]:
    version = anchor_policy_version(policy)
    required = {
        "paper_id",
        "chunk_id",
        "quote",
        "source_content_hash",
        "policy_version",
    }
    if not isinstance(anchor, Mapping) or set(anchor) != required:
        raise AnchorValidationError(
            "ANCHOR_FIELDS", "source anchor field set must be exact"
        )
    if anchor.get("paper_id") != paper_id:
        raise AnchorValidationError("ANCHOR_PAPER_MISMATCH", "cross-paper anchor")
    if anchor.get("source_content_hash") != source_content_hash:
        raise AnchorValidationError("ANCHOR_SOURCE_HASH_MISMATCH", "source hash mismatch")
    if anchor.get("policy_version") != version:
        raise AnchorValidationError("ANCHOR_POLICY_VERSION", "policy version mismatch")
    chunk_id = _text(anchor.get("chunk_id"), "anchor.chunk_id")
    quote = _text(anchor.get("quote"), "anchor.quote")
    matches = [chunk for chunk in chunks if _chunk_id(chunk) == chunk_id]
    if len(matches) != 1:
        raise AnchorValidationError(
            "ANCHOR_CHUNK_NOT_UNIQUE", "chunk must exist exactly once"
        )
    text = _chunk_text(matches[0])
    if text is None or not quote_in_text(quote, text):
        raise AnchorValidationError(
            "ANCHOR_QUOTE_NOT_LITERAL", "quote is not literal source text"
        )
    return anchor


def _assessment_shell(
    *,
    template: Mapping[str, Any],
    template_contract_ref: Mapping[str, Any],
    anchor_policy_ref: Mapping[str, Any],
    card_artifact_ref: Mapping[str, Any],
    source_chunks_manifest_ref: Mapping[str, Any],
    build_status: str,
    block_reason_codes: Sequence[str],
    slots: Sequence[Mapping[str, Any]],
    extraction_coverage: str,
    computed_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "required-slot-assessment-v1",
        "template_contract_ref": dict(template_contract_ref),
        "template_version": template.get("template_version"),
        "anchor_normalization_policy_ref": dict(anchor_policy_ref),
        "assessment_build_status": build_status,
        "block_reason_codes": list(block_reason_codes),
        "card_artifact_ref": dict(card_artifact_ref),
        "source_chunks_manifest_ref": dict(source_chunks_manifest_ref),
        "slots": [dict(item) for item in slots],
        "extraction_coverage": extraction_coverage,
        "assessment_policy_ref": "Memorive-FACT-IDENTITY-required-slot-assessment-r0.4",
        "producer_module": "EVIDENCE_EXTRACTION",
        "producer_version": "fact_identity-a2",
        "computed_at": computed_at,
    }


def _aggregate_slot_coverage(states: Sequence[str]) -> str:
    if not states or all(value == "not_assessed" for value in states):
        return "not_assessed"
    if any(value == "extraction_gap" for value in states):
        return "suspicious_gap"
    if any(value == "not_assessed" for value in states):
        return "partial"
    return "sufficient"


def build_required_slot_assessment(
    *,
    template: Mapping[str, Any],
    mode: str,
    template_contract_ref: Mapping[str, Any],
    anchor_policy: Mapping[str, Any],
    anchor_policy_ref: Mapping[str, Any],
    card_artifact_ref: Mapping[str, Any],
    source_chunks_manifest_ref: Mapping[str, Any],
    paper_id: str,
    source_content_hash: str,
    card_review_status: str,
    chunks: Sequence[Mapping[str, Any]],
    observations: Mapping[str, Mapping[str, Any]],
    absence_policy_payload: Mapping[str, Any] | None = None,
    absence_policy_ref: Mapping[str, Any] | None = None,
    computed_at: str | None = None,
    audit_events: MutableSequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for ref in (
        template_contract_ref,
        anchor_policy_ref,
        card_artifact_ref,
        source_chunks_manifest_ref,
    ):
        validate_artifact_ref(ref)
    validate_review_status(card_review_status)
    _sha(source_content_hash, "source_content_hash")
    validate_anchor_policy(anchor_policy)
    stamp = computed_at or now_iso()
    try:
        validate_template_contract(template, mode=mode)
    except TemplateBlockedError as exc:
        _audit(
            audit_events,
            "assessment_build_blocked",
            "REQUIRED_SLOT_TEMPLATE_NOT_APPROVED",
            detail_code=exc.code,
            paper_id=paper_id,
        )
        return _assessment_shell(
            template=template,
            template_contract_ref=template_contract_ref,
            anchor_policy_ref=anchor_policy_ref,
            card_artifact_ref=card_artifact_ref,
            source_chunks_manifest_ref=source_chunks_manifest_ref,
            build_status="blocked",
            block_reason_codes=[
                "REQUIRED_SLOT_TEMPLATE_NOT_APPROVED",
                exc.code,
            ],
            slots=[],
            extraction_coverage="not_assessed",
            computed_at=stamp,
        )
    records: list[dict[str, Any]] = []
    for slot in template["required_slots"]:
        slot_id = slot["slot_id"]
        observation = observations.get(slot_id, {"state": "not_assessed"})
        state = observation.get("state")
        if state not in SLOT_STATES:
            raise ContractError("SLOT_STATE_INVALID", f"invalid state {state!r}")
        record = {
            "slot_id": slot_id,
            "slot_path": slot["slot_path"],
            "state": state,
            "source_anchor_refs": [],
            "absence_check_ref": None,
            "not_applicable_rule_ref": None,
            "reason_codes": list(observation.get("reason_codes", [])),
        }
        if state == "present":
            anchor = observation.get("source_anchor")
            validate_source_anchor(
                anchor,
                paper_id=paper_id,
                source_content_hash=source_content_hash,
                chunks=chunks,
                policy=anchor_policy,
            )
            record["source_anchor_refs"] = [dict(anchor)]
        elif state == "absent_in_source":
            if absence_policy_payload is None or absence_policy_ref is None:
                raise AbsenceEvidenceError(
                    "ABSENCE_POLICY_MISSING", "absence policy is required"
                )
            payload = observation.get("absence_check_payload")
            ref = observation.get("absence_check_ref")
            validate_absence_check_evidence(
                payload,
                ref,
                template_contract_ref=template_contract_ref,
                policy_payload=absence_policy_payload,
                policy_ref=absence_policy_ref,
                source_artifact_ref=source_chunks_manifest_ref,
                source_content_hash=source_content_hash,
                slot_id=slot_id,
            )
            record["absence_check_ref"] = dict(ref)
        elif state == "not_applicable":
            rule = observation.get("not_applicable_rule_ref")
            if rule not in slot.get("not_applicable_rule_ids", []):
                raise ContractError(
                    "NOT_APPLICABLE_RULE_INVALID", "template rule does not authorize NA"
                )
            record["not_applicable_rule_ref"] = rule
        elif state == "extraction_gap" and not record["reason_codes"]:
            raise ContractError(
                "EXTRACTION_GAP_REASON_MISSING", "extraction_gap requires reason_codes"
            )
        records.append(record)
    coverage = _aggregate_slot_coverage([
        item["state"]
        for item, slot in zip(records, template["required_slots"])
        if slot["assessment_required"]
    ])
    result = _assessment_shell(
        template=template,
        template_contract_ref=template_contract_ref,
        anchor_policy_ref=anchor_policy_ref,
        card_artifact_ref=card_artifact_ref,
        source_chunks_manifest_ref=source_chunks_manifest_ref,
        build_status="buildable",
        block_reason_codes=[],
        slots=records,
        extraction_coverage=coverage,
        computed_at=stamp,
    )
    _audit(
        audit_events,
        "required_slot_assessment_built",
        "BUILDABLE",
        paper_id=paper_id,
        extraction_coverage=coverage,
    )
    return result


__all__ = [
    "AnchorValidationError",
    "SLOT_STATES",
    "TemplateBlockedError",
    "anchor_policy_version",
    "build_required_slot_assessment",
    "validate_anchor_policy",
    "validate_source_anchor",
    "validate_template_contract",
]
