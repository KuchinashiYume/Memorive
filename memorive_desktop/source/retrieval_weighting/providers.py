"""Six-provider score contract for the SEMANTIC-AGGREGATION Shadow candidate."""

from __future__ import annotations

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
    require_exact_keys,
    require_finite_number,
)


PROVIDERS = ("Similarity", "Semantic", "Manual", "Rule", "Note", "Citation")
RELEVANCE_PROVIDERS = ("Similarity", "Semantic")
AUTHORITY_PROVIDERS = ("Manual", "Rule", "Note", "Citation")
PROVIDER_STATES = ("observed", "missing", "not_applicable", "invalid")

_RECORD_KEYS = {
    "candidate_id",
    "provider",
    "score",
    "state",
    "reason_code",
    "evidence_locator",
    "source_hash",
    "normalization_profile",
    "provider_version",
    "observed_at",
    "adapter_evidence",
    "content_hash",
}
_STRICT_EVIDENCE_KEYS = {
    "Similarity": {
        "legacy_locator",
        "legacy_source_hash",
        "channel",
        "normalization_profile",
        "observed_at",
        "reranker_state",
    },
    "Semantic": {
        "field_vector_locator",
        "field_vector_hash",
        "query_dimension_hash",
        "field_contribution_refs",
        "normalization_profile",
        "observed_at",
        "semantic_qualification",
    },
    "Manual": {
        "who",
        "when",
        "item",
        "reason",
        "event_locator",
        "event_hash",
        "normalization_profile",
    },
    "Rule": {
        "authority_locator",
        "authority_hash",
        "field_name",
        "observed_value_ref",
        "normalization_profile",
        "observed_at",
    },
    "Note": {
        "provenance_locator",
        "provenance_hash",
        "signal_kind",
        "normalization_profile",
        "observed_at",
    },
    "Citation": {
        "writing_record_locator",
        "writing_record_hash",
        "citation_type",
        "record_kind",
        "normalization_profile",
        "observed_at",
    },
}


def validate_provider_score_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact ProviderScoreRecord and return a defensive copy."""

    if not isinstance(record, Mapping):
        raise ContractViolation("provider_score_record must be an object")
    result = deepcopy(dict(record))
    reject_scorer_or_oracle_fields(result, "provider_score_record")
    require_exact_keys(result, _RECORD_KEYS, "provider_score_record")
    require_non_empty_string(result["candidate_id"], "provider_score_record.candidate_id")
    if result["provider"] not in PROVIDERS:
        raise ContractViolation("provider_score_record.provider must be one of the frozen six")
    if result["state"] not in PROVIDER_STATES:
        raise ContractViolation("provider_score_record.state must be one of the frozen four")
    reason_code = require_non_empty_string(
        result["reason_code"], "provider_score_record.reason_code"
    )
    require_non_empty_string(
        result["provider_version"], "provider_score_record.provider_version"
    )
    if result["state"] == "observed":
        require_finite_number(
            result["score"],
            "provider_score_record.score",
            minimum=0.0,
            maximum=1.0,
        )
        require_non_empty_string(
            result["evidence_locator"], "provider_score_record.evidence_locator"
        )
        validate_hash_descriptor(result["source_hash"], "provider_score_record.source_hash")
        require_non_empty_string(
            result["normalization_profile"],
            "provider_score_record.normalization_profile",
        )
        require_utc_timestamp(result["observed_at"], "provider_score_record.observed_at")
        locator, source_hash, normalization, observed_at = _strict_observed_evidence(
            result["provider"], result["adapter_evidence"]
        )
        if locator != result["evidence_locator"]:
            raise ContractViolation("provider evidence locator differs from strict adapter evidence")
        if source_hash != result["source_hash"]:
            raise ContractViolation("provider source hash differs from strict adapter evidence")
        if normalization != result["normalization_profile"]:
            raise ContractViolation("provider normalization differs from strict adapter evidence")
        if observed_at != result["observed_at"]:
            raise ContractViolation("provider observed_at differs from strict adapter evidence")
    else:
        for name in (
            "score",
            "evidence_locator",
            "source_hash",
            "normalization_profile",
            "observed_at",
            "adapter_evidence",
        ):
            if result[name] is not None:
                raise ContractViolation(
                    f"provider_score_record.{name} must be null when state is not observed"
                )
    if result["provider"] == "Citation" and "citation_count" in reason_code.casefold():
        raise ContractViolation("Citation provider cannot consume a source citation count")
    validate_hash_descriptor(result["content_hash"], "provider_score_record.content_hash")
    return verify_hashed_payload(result, "provider_score_record")


def validate_provider_set(
    records: Sequence[Mapping[str, Any]], candidate_id: str | None = None
) -> list[dict[str, Any]]:
    """Require exactly one record for each frozen provider for one candidate."""

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ContractViolation("provider records must be an array")
    validated = [validate_provider_score_record(item) for item in records]
    if len(validated) != len(PROVIDERS):
        raise ContractViolation("provider set must contain exactly six records")
    by_provider: dict[str, dict[str, Any]] = {}
    for record in validated:
        provider = record["provider"]
        if provider in by_provider:
            raise ContractViolation(
                "provider set contains a duplicate provider", context={"provider": provider}
            )
        by_provider[provider] = record
    if set(by_provider) != set(PROVIDERS):
        raise ContractViolation("provider set does not match the frozen six-provider set")
    observed_candidates = {record["candidate_id"] for record in validated}
    if len(observed_candidates) != 1:
        raise ContractViolation("provider set must bind exactly one candidate_id")
    actual_candidate = next(iter(observed_candidates))
    if candidate_id is not None:
        require_non_empty_string(candidate_id, "candidate_id")
        if actual_candidate != candidate_id:
            raise ContractViolation("provider set candidate_id does not match the caller")
    return [deepcopy(by_provider[provider]) for provider in PROVIDERS]


def validate_provider_score_set(
    records: Sequence[Mapping[str, Any]], candidate_id: str | None = None
) -> list[dict[str, Any]]:
    """Compatibility alias for the frozen provider-set validator."""

    return validate_provider_set(records, candidate_id=candidate_id)


def _build_provider_score_record(
    *,
    candidate_id: str,
    provider: str,
    score: float | None,
    state: str,
    reason_code: str,
    evidence_locator: str | None,
    source_hash: Mapping[str, Any] | None,
    normalization_profile: str | None,
    provider_version: str,
    observed_at: str | None,
    adapter_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Private record constructor; callers must pass a validated adapter result."""

    payload = {
        "candidate_id": candidate_id,
        "provider": provider,
        "score": score,
        "state": state,
        "reason_code": reason_code,
        "evidence_locator": evidence_locator,
        "source_hash": deepcopy(dict(source_hash)) if source_hash is not None else None,
        "normalization_profile": normalization_profile,
        "provider_version": provider_version,
        "observed_at": observed_at,
        "adapter_evidence": deepcopy(dict(adapter_evidence)) if adapter_evidence is not None else None,
    }
    return validate_provider_score_record(make_hashed_payload(payload))


def build_absent_provider_score_record(
    *,
    candidate_id: str,
    provider: str,
    state: str,
    reason_code: str,
    provider_version: str,
) -> dict[str, Any]:
    """Build an explicit non-observed provider record without fabricating evidence."""

    if state not in {"missing", "not_applicable", "invalid"}:
        raise ContractViolation("absence builder accepts only non-observed provider states")
    return _build_provider_score_record(
        candidate_id=candidate_id,
        provider=provider,
        score=None,
        state=state,
        reason_code=reason_code,
        evidence_locator=None,
        source_hash=None,
        normalization_profile=None,
        provider_version=provider_version,
        observed_at=None,
        adapter_evidence=None,
    )


def build_provider_score_record(
    *,
    candidate_id: str,
    provider: str,
    score: float | None,
    state: str,
    reason_code: str,
    evidence_locator: str | None,
    source_hash: Mapping[str, Any] | None,
    normalization_profile: str | None,
    provider_version: str,
    observed_at: str | None,
) -> dict[str, Any]:
    """Compatibility path restricted to explicit non-observed records.

    Observed construction is intentionally unavailable here because generic
    locator/hash inputs cannot prove provider-specific source authority.
    """

    if state == "observed":
        raise ContractViolation(
            "observed ProviderScoreRecord must use build_strict_provider_score_record"
        )
    if any(
        item is not None
        for item in (score, evidence_locator, source_hash, normalization_profile, observed_at)
    ):
        raise ContractViolation("non-observed generic provider inputs must be null")
    return build_absent_provider_score_record(
        candidate_id=candidate_id,
        provider=provider,
        state=state,
        reason_code=reason_code,
        provider_version=provider_version,
    )


def _strict_observed_evidence(provider: str, evidence: Mapping[str, Any]) -> tuple[str, dict[str, str], str, str]:
    if not isinstance(evidence, Mapping):
        raise ContractViolation(f"{provider} observed evidence must be an object")
    value = deepcopy(dict(evidence))
    reject_scorer_or_oracle_fields(value, f"{provider}.evidence")
    require_exact_keys(value, _STRICT_EVIDENCE_KEYS[provider], f"{provider}.evidence")

    if provider == "Similarity":
        locator = require_non_empty_string(value["legacy_locator"], "Similarity.evidence.legacy_locator")
        validate_hash_descriptor(value["legacy_source_hash"], "Similarity.evidence.legacy_source_hash")
        require_non_empty_string(value["channel"], "Similarity.evidence.channel")
        if value["reranker_state"] != "disabled":
            raise ContractViolation("Similarity reranker must remain disabled in structural A")
        observed_at = require_utc_timestamp(value["observed_at"], "Similarity.evidence.observed_at")
    elif provider == "Semantic":
        locator = require_non_empty_string(
            value["field_vector_locator"], "Semantic.evidence.field_vector_locator"
        )
        validate_hash_descriptor(value["field_vector_hash"], "Semantic.evidence.field_vector_hash")
        validate_hash_descriptor(value["query_dimension_hash"], "Semantic.evidence.query_dimension_hash")
        refs = value["field_contribution_refs"]
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
            raise ContractViolation("Semantic.evidence.field_contribution_refs must be non-empty")
        normalized_refs = [require_non_empty_string(item, "Semantic.evidence.field_contribution_refs[]") for item in refs]
        if normalized_refs != sorted(set(normalized_refs)):
            raise ContractViolation("Semantic field contribution refs must be sorted and unique")
        if value["semantic_qualification"] != "NOT_ASSESSED":
            raise ContractViolation("structural Semantic evidence must remain NOT_ASSESSED")
        observed_at = require_utc_timestamp(value["observed_at"], "Semantic.evidence.observed_at")
    elif provider == "Manual":
        for name in ("who", "item", "reason"):
            require_non_empty_string(value[name], f"Manual.evidence.{name}")
        require_utc_timestamp(value["when"], "Manual.evidence.when")
        locator = require_non_empty_string(value["event_locator"], "Manual.evidence.event_locator")
        if not locator.startswith(("KNOWLEDGE_ADMISSION:", "RUNTIME_LOG:", "DECISION_LOG:", "ARTIFACT_REGISTRY:")):
            raise ContractViolation("Manual evidence must bind KNOWLEDGE_ADMISSION/RUNTIME_LOG/DECISION_LOG/ARTIFACT_REGISTRY event authority")
        validate_hash_descriptor(value["event_hash"], "Manual.evidence.event_hash")
        observed_at = value["when"]
    elif provider == "Rule":
        locator = require_non_empty_string(
            value["authority_locator"], "Rule.evidence.authority_locator"
        )
        validate_hash_descriptor(value["authority_hash"], "Rule.evidence.authority_hash")
        require_non_empty_string(value["field_name"], "Rule.evidence.field_name")
        require_non_empty_string(value["observed_value_ref"], "Rule.evidence.observed_value_ref")
        observed_at = require_utc_timestamp(value["observed_at"], "Rule.evidence.observed_at")
    elif provider == "Note":
        locator = require_non_empty_string(
            value["provenance_locator"], "Note.evidence.provenance_locator"
        )
        validate_hash_descriptor(value["provenance_hash"], "Note.evidence.provenance_hash")
        require_non_empty_string(value["signal_kind"], "Note.evidence.signal_kind")
        observed_at = require_utc_timestamp(value["observed_at"], "Note.evidence.observed_at")
    else:
        locator = require_non_empty_string(
            value["writing_record_locator"], "Citation.evidence.writing_record_locator"
        )
        validate_hash_descriptor(
            value["writing_record_hash"], "Citation.evidence.writing_record_hash"
        )
        require_non_empty_string(value["citation_type"], "Citation.evidence.citation_type")
        if value["record_kind"] != "user_writing_citation":
            raise ContractViolation("Citation must bind the user's writing citation record")
        observed_at = require_utc_timestamp(value["observed_at"], "Citation.evidence.observed_at")

    normalization = require_non_empty_string(
        value["normalization_profile"], f"{provider}.evidence.normalization_profile"
    )
    return locator, typed_payload_hash(value), normalization, observed_at


def build_strict_provider_score_record(
    *,
    candidate_id: str,
    provider: str,
    score: float | None,
    state: str,
    reason_code: str,
    evidence: Mapping[str, Any] | None,
    provider_version: str,
) -> dict[str, Any]:
    """Build a provider record through its source-specific evidence adapter."""

    if provider not in PROVIDERS:
        raise ContractViolation("strict provider adapter received an unknown provider")
    if state == "observed":
        if evidence is None:
            raise ContractViolation(f"observed {provider} requires strict evidence")
        locator, source_hash, normalization, observed_at = _strict_observed_evidence(
            provider, evidence
        )
    else:
        if evidence is not None:
            raise ContractViolation(f"non-observed {provider} evidence must be null")
        locator = None
        source_hash = None
        normalization = None
        observed_at = None
    return _build_provider_score_record(
        candidate_id=candidate_id,
        provider=provider,
        score=score,
        state=state,
        reason_code=reason_code,
        evidence_locator=locator,
        source_hash=source_hash,
        normalization_profile=normalization,
        provider_version=provider_version,
        observed_at=observed_at,
        adapter_evidence=evidence,
    )


__all__ = [
    "AUTHORITY_PROVIDERS",
    "PROVIDERS",
    "PROVIDER_STATES",
    "RELEVANCE_PROVIDERS",
    "build_absent_provider_score_record",
    "build_provider_score_record",
    "build_strict_provider_score_record",
    "validate_provider_score_record",
    "validate_provider_score_set",
    "validate_provider_set",
]
