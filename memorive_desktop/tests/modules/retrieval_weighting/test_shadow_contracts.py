from __future__ import annotations

from copy import deepcopy
import unittest

from retrieval_weighting.canonical import sha256_bytes
from retrieval_weighting.contracts import make_hashed_payload, typed_payload_hash
from retrieval_weighting.derived_penalty import UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED, apply_authority_penalty, build_derived_penalty_record, validate_derived_penalty_against_policy, validate_derived_penalty_record
from retrieval_weighting.errors import ContractViolation
from retrieval_weighting.field_registry import get_field_registry
from retrieval_weighting.providers import AUTHORITY_PROVIDERS, PROVIDERS, RELEVANCE_PROVIDERS, build_absent_provider_score_record, build_provider_score_record, build_strict_provider_score_record, validate_provider_score_record, validate_provider_set
from retrieval_weighting.query_dimensions import (
    build_query_dimension_profile,
    validate_query_dimension_profile,
)
from retrieval_weighting.shadow_contracts import reject_scorer_or_oracle_fields
from retrieval_weighting.shadow_contracts import validate_retrieval_policy_snapshot
def _hash(label: str) -> dict[str, str]:
    return typed_payload_hash({"label": label})


def _penalty_policy() -> dict:
    return {
        "policy_version": "derived-penalty-v1",
        "type_penalties": {"analysis": 0.8, "half": 0.5, "summary": 0.8},
        "unknown_provenance_issue": UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED,
        "override_contract_version": "override-v1",
        "override_event_allowlist": [
            {
                "event_locator": "RUNTIME_LOG:fixture-event-1",
                "event_hash": _hash("override-event"),
            }
        ],
        "authority_only": True,
    }


def _query_profile() -> dict:
    registry = get_field_registry()
    return build_query_dimension_profile(
        query_hash=_hash("query"),
        field_registry_hash=registry["content_hash"],
        allocations=[
            {
                "field_id": "author_conclusion",
                "allocation": 0.4,
                "reason_code": "CONCLUSION_INTENT",
                "evidence_ref": "fixture://query/1#conclusion",
            },
            {
                "field_id": "method/{item}",
                "allocation": 0.6,
                "reason_code": "METHOD_INTENT",
                "evidence_ref": "fixture://query/1#method",
            },
        ],
        allocation_method="frozen_keyword_soft_allocation_v1",
        fallback_floor=0.0,
        missing_fields=["author_limitations_outlook/limitations/{item}/quote"],
        reason_codes=["CONCLUSION_INTENT", "METHOD_INTENT"],
        policy_version="query-policy-v1",
        frozen_registry=registry,
    )


def _provider(provider: str, *, state: str = "observed") -> dict:
    if state != "observed":
        return build_absent_provider_score_record(
            candidate_id="card-001",
            provider=provider,
            state=state,
            reason_code=state.upper(),
            provider_version=f"{provider.lower()}-v1",
        )
    common = {
        "normalization_profile": "minmax-fixture-v1",
        "observed_at": "2026-08-03T00:00:00Z",
    }
    evidence = {
        "Similarity": {
            "legacy_locator": "fixture://provider/similarity",
            "legacy_source_hash": _hash("Similarity"),
            "channel": "legacy",
            **common,
            "reranker_state": "disabled",
        },
        "Semantic": {
            "field_vector_locator": "fixture://provider/semantic",
            "field_vector_hash": _hash("Semantic"),
            "query_dimension_hash": _hash("query-dimension"),
            "field_contribution_refs": ["fixture://field/author_conclusion"],
            **common,
            "semantic_qualification": "NOT_ASSESSED",
        },
        "Manual": {
            "who": "user",
            "when": "2026-08-03T00:00:00Z",
            "item": "card-001",
            "reason": "fixture preference",
            "event_locator": "RUNTIME_LOG:manual-event",
            "event_hash": _hash("Manual"),
            "normalization_profile": "minmax-fixture-v1",
        },
        "Rule": {
            "authority_locator": "fixture://provider/rule-authority",
            "authority_hash": _hash("Rule"),
            "field_name": "publication_year",
            "observed_value_ref": "fixture://provider/rule-value",
            **common,
        },
        "Note": {
            "provenance_locator": "fixture://provider/note-provenance",
            "provenance_hash": _hash("Note"),
            "signal_kind": "research_trace",
            **common,
        },
        "Citation": {
            "writing_record_locator": "fixture://provider/writing-citation",
            "writing_record_hash": _hash("Citation"),
            "citation_type": "supports",
            "record_kind": "user_writing_citation",
            **common,
        },
    }[provider]
    return build_strict_provider_score_record(
        candidate_id="card-001",
        provider=provider,
        score=0.5,
        state="observed",
        reason_code="FIXTURE_OBSERVED",
        evidence=evidence,
        provider_version=f"{provider.lower()}-v1",
    )


def _unknown_penalty() -> dict:
    return build_derived_penalty_record(
        candidate_id="card-001",
        provenance_state="unknown",
        derived_type=None,
        parent_refs=[],
        source_refs=["fixture://partial-source"],
        generation_basis_ref=None,
        producer_version=None,
        lineage_refs=[],
        penalty_policy=_penalty_policy(),
        design_prior=0.0,
        base_penalty=0.0,
        override=None,
        effective_penalty=0.0,
        authority_blocked=True,
        issue_codes=[UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED],
    )


def _snapshot() -> dict:
    query_pack_hash = _hash("query-pack")
    threshold_function_hash = _hash("threshold-function")
    payload = {
        "object_id": "RPS-001",
        "schema_version": "2.0",
        "revision": 1,
        "producer": "SEMANTIC-AGGREGATION/CROSS",
        "consumers": ["MODEL_EVALUATION", "RETRIEVAL", "RETRIEVAL_WEIGHTING"],  # ascending, as the contract requires
        "state": "candidate",
        "parent_refs": ["ServiceContracts-COMPLETION"],
        "provenance_refs": ["fixture://h0/authority"],
        "supersedes": None,
        "immutable_fields": [
            "object_id",
            "schema_version",
            "revision",
            "content_hash",
            "producer",
            "parent_refs",
            "provenance_refs",
            "supersedes",
        ],
        "failure_semantics": {
            "BLOCKED": "fail_closed",
            "ERROR": "quarantined",
            "FAIL": "fail_closed",
            "NOT_ASSESSED": "no_eligibility",
        },
        "activation_authorized": False,
        "semantic_shadow_eligible": False,
        "semantic_qualification": "NOT_ASSESSED",
        "structural_only": True,
        "evaluation_bindings": {
            "query_pack_hash": query_pack_hash,
            "reference_pack_hash": _hash("reference-pack"),
            "evaluation_tool_hash": _hash("evaluation-tool"),
            "threshold_function_hash": threshold_function_hash,
        },
        "field_export_identity": {
            "field_registry_hash": _hash("field-registry"),
            "field_index_manifest_hash": _hash("field-index"),
            "field_export_hash": _hash("field-export"),
            "embedding_profile_id": "offline:fixture-v1-d4",
            "embedding_dimension": 4,
            "embedding_normalization": "l2",
            "production_eligible": False,
        },
        "legacy_baseline_identity": {
            "baseline_commit": "d" * 40,
            "code_hashes": {
                "retrieval/retriever.py": sha256_bytes(b"retriever"),
            },
            "route_id": "baseline-v1",
            "index_ref": "fixture://legacy-index",
            "index_manifest_hash": _hash("legacy-index"),
            "cutoff_ref": "fixture://cutoff/1",
            "read_only": True,
        },
        "query_policy": {
            "policy_version": "query-policy-v1",
            "query_set_hash": query_pack_hash,
            "allocation_method": "soft-allocation-v1",
            "fallback_floor": 0.0,
            "missing_field_policy": "skip_and_renormalize",
            "allocation_tie_break": "field_id_ascending",
        },
        "provider_policy": {
            "providers": list(PROVIDERS),
            "provider_versions": {provider: f"{provider.lower()}-v1" for provider in PROVIDERS},
            "normalization_profiles": {provider: "minmax-fixture-v1" for provider in PROVIDERS},
            "weights": {
                "Similarity": 0.45,
                "Semantic": 0.20,
                "Manual": 0.15,
                "Rule": 0.10,
                "Note": 0.07,
                "Citation": 0.03,
            },
            "states": ["observed", "missing", "not_applicable", "invalid"],
            "reranker_state": "disabled",
            "model_state": "disabled",
        },
        "aggregation_policy": {
            "baseline_policy_id": "policy_baseline_v6_1",
            "shadow_policy_id": "policy_shadow_availability_aware_v1",
            "relevance_providers": list(RELEVANCE_PROVIDERS),
            "authority_providers": list(AUTHORITY_PROVIDERS),
            "missing_data_policy": "availability_aware",
            "authority_mass_cap": 0.35,
            "rrf_k": 60,
            "channel_caps": {"field:limitations": 20, "legacy": 20},
            "per_card_cap": 3,
            "top_k": 10,
            "mmr_lambda": 0.7,
            "counterevidence_slots": 2,
            "counter_minimum_relevance": 0.5,
            "counter_per_source_cap": 1,
            "counter_per_paper_cap": 3,
            "tie_break": "candidate_id_ascending",
            "seed": 0,
            "token_budget": 0,
        },
        "derived_penalty_policy": _penalty_policy(),
        "scope_policy": {
            "active_only": True,
            "explicit_scope_required": True,
            "rights_required": True,
            "allowlist_refs": ["fixture://scope/allowlist"],
        },
        "side_effect_policy": {
            "shadow_only": True,
            "production_writes_allowed": False,
            "route_changes_allowed": False,
            "pointer_changes_allowed": False,
            "disable_switch": "SHADOW_DISABLED",
            "environment_id": "offline-fixture",
            "output_manifest_ref": "fixture://output/manifest",
        },
        "threshold_policy": {
            "function_version": "baseline-relative-v1",
            "function_hash": threshold_function_hash,
            "baseline_relative": True,
            "candidate_output_not_consumed": True,
            "receipt_required_before_candidate": True,
        },
        "hash_kind": "rfc8785_jcs_sha256",
    }
    return make_hashed_payload(payload)


def test_query_profile_is_deterministic_and_hash_bound() -> None:
    first = _query_profile()
    second = _query_profile()
    assert first == second
    tampered = deepcopy(first)
    tampered["allocations"][0]["allocation"] = 0.5
    with unittest.TestCase().assertRaises(ContractViolation):
        build_query_dimension_profile(
            query_hash=tampered["query_hash"],
            field_registry_hash=tampered["field_registry_hash"],
            allocations=tampered["allocations"],
            allocation_method=tampered["allocation_method"],
            fallback_floor=tampered["fallback_floor"],
            missing_fields=tampered["missing_fields"],
            reason_codes=tampered["reason_codes"],
            policy_version=tampered["policy_version"],
            frozen_registry=get_field_registry(),
        )


def test_query_profile_rejects_unknown_field_and_registry_hash_drift() -> None:
    registry = get_field_registry()
    with unittest.TestCase().assertRaises(ContractViolation):
        build_query_dimension_profile(
            query_hash=_hash("query"),
            field_registry_hash=registry["content_hash"],
            allocations=[
                {
                    "field_id": "invented_field",
                    "allocation": 1.0,
                    "reason_code": "INVENTED",
                    "evidence_ref": "fixture://invented",
                }
            ],
            allocation_method="fixture-v1",
            fallback_floor=0.0,
            missing_fields=[],
            reason_codes=["INVENTED"],
            policy_version="query-policy-v1",
            frozen_registry=registry,
        )
    drift_payload = {key: value for key, value in registry.items() if key != "content_hash"}
    drift_payload["revision"] = 2
    drift_registry = make_hashed_payload(drift_payload)
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_query_dimension_profile(_query_profile(), frozen_registry=drift_registry)


def test_provider_set_requires_exact_six_and_four_state_null_semantics() -> None:
    records = [_provider(provider, state="missing" if provider == "Note" else "observed") for provider in PROVIDERS]
    validated = validate_provider_set(list(reversed(records)), candidate_id="card-001")
    assert [item["provider"] for item in validated] == list(PROVIDERS)
    assert validated[4]["score"] is None
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_provider_set(records[:-1])


def test_recursive_oracle_and_gold_fields_are_rejected() -> None:
    for payload in (
        {"nested": [{"gold_manifest_hash": "x"}]},
        {"nested": {"oracle": "x"}},
        {"nested": {"answer_key": "x"}},
        {"nested": {"scorer_version": "x"}},
        {"source_refs": ["fixture://private-gold/item"]},
    ):
        with unittest.TestCase().assertRaises(ContractViolation):
            reject_scorer_or_oracle_fields(payload)


def test_strict_provider_adapters_fail_closed_by_source_type() -> None:
    manual = {
        "who": "user",
        "when": "2026-08-03T00:00:00Z",
        "item": "card-001",
        "reason": "explicit preference",
        "event_locator": "RUNTIME_LOG:event-1",
        "event_hash": _hash("manual-event"),
        "normalization_profile": "manual-v1",
    }
    record = build_strict_provider_score_record(
        candidate_id="card-001",
        provider="Manual",
        score=0.6,
        state="observed",
        reason_code="EXPLICIT_MANUAL_EVENT",
        evidence=manual,
        provider_version="manual-v1",
    )
    assert record["provider"] == "Manual"
    with unittest.TestCase().assertRaises(ContractViolation):
        build_provider_score_record(
            candidate_id="card-001",
            provider="Manual",
            score=0.6,
            state="observed",
            reason_code="GENERIC_BYPASS",
            evidence_locator="fixture://manual/no-authority",
            source_hash=_hash("generic-manual"),
            normalization_profile="manual-v1",
            provider_version="manual-v1",
            observed_at="2026-08-03T00:00:00Z",
        )
    forged = deepcopy(record)
    forged["adapter_evidence"] = None
    forged = make_hashed_payload(
        {key: value for key, value in forged.items() if key != "content_hash"}
    )
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_provider_score_record(forged)
    incomplete = deepcopy(manual)
    incomplete.pop("who")
    with unittest.TestCase().assertRaises(ContractViolation):
        build_strict_provider_score_record(
            candidate_id="card-001",
            provider="Manual",
            score=0.6,
            state="observed",
            reason_code="EXPLICIT_MANUAL_EVENT",
            evidence=incomplete,
            provider_version="manual-v1",
        )
    gold_locator = deepcopy(manual)
    gold_locator["event_locator"] = "RUNTIME_LOG:private-gold-event"
    with unittest.TestCase().assertRaises(ContractViolation):
        build_strict_provider_score_record(
            candidate_id="card-001",
            provider="Manual",
            score=0.6,
            state="observed",
            reason_code="FORBIDDEN_LOCATOR",
            evidence=gold_locator,
            provider_version="manual-v1",
        )
    rule_without_authority = {
        "authority_hash": _hash("rule-authority"),
        "field_name": "publication_year",
        "observed_value_ref": "fixture://rule/value",
        "normalization_profile": "rule-v1",
        "observed_at": "2026-08-03T00:00:00Z",
    }
    with unittest.TestCase().assertRaises(ContractViolation):
        build_strict_provider_score_record(
            candidate_id="card-001",
            provider="Rule",
            score=0.4,
            state="observed",
            reason_code="RULE_METADATA",
            evidence=rule_without_authority,
            provider_version="rule-v1",
        )
    note_with_body = {
        "provenance_locator": "fixture://note/event",
        "provenance_hash": _hash("note-event"),
        "signal_kind": "research_trace",
        "normalization_profile": "note-v1",
        "observed_at": "2026-08-03T00:00:00Z",
        "note_body": "must not enter provider evidence",
    }
    with unittest.TestCase().assertRaises(ContractViolation):
        build_strict_provider_score_record(
            candidate_id="card-001",
            provider="Note",
            score=0.4,
            state="observed",
            reason_code="NOTE_TRACE",
            evidence=note_with_body,
            provider_version="note-v1",
        )
    citation_count = {
        "writing_record_locator": "fixture://paper/metrics",
        "writing_record_hash": _hash("citation-count"),
        "citation_type": "count",
        "record_kind": "source_citation_count",
        "normalization_profile": "citation-v1",
        "observed_at": "2026-08-03T00:00:00Z",
    }
    with unittest.TestCase().assertRaises(ContractViolation):
        build_strict_provider_score_record(
            candidate_id="card-001",
            provider="Citation",
            score=0.9,
            state="observed",
            reason_code="SOURCE_METRIC",
            evidence=citation_count,
            provider_version="citation-v1",
        )


def test_unknown_provenance_blocks_authority_but_preserves_relevance() -> None:
    result = apply_authority_penalty(
        relevance_subtotal=0.42,
        authority_subtotal=0.31,
        penalty_record=_unknown_penalty(),
        penalty_policy=_penalty_policy(),
    )
    assert result["relevance_subtotal"] == 0.42
    assert result["penalized_authority_subtotal"] == 0.0
    assert result["combined_score"] == 0.42


def test_override_is_fail_closed_without_every_evidence_field() -> None:
    incomplete = {
        "who": "user",
        "when": "2026-08-03T00:00:00Z",
        "item": "card-001",
        "old": 0.8,
        "new": 0.7,
        "reason": "frozen fixture",
    }
    with unittest.TestCase().assertRaises(ContractViolation):
        build_derived_penalty_record(
            candidate_id="card-001",
            provenance_state="derived",
            derived_type="summary",
            parent_refs=["card-original"],
            source_refs=["fixture://source"],
            generation_basis_ref="fixture://basis",
            producer_version="producer-v1",
            lineage_refs=["fixture://lineage"],
            penalty_policy=_penalty_policy(),
            design_prior=0.8,
            base_penalty=0.8,
            override=incomplete,
            effective_penalty=0.7,
            authority_blocked=False,
            issue_codes=[],
        )


def test_complete_override_and_authority_only_application() -> None:
    record = build_derived_penalty_record(
        candidate_id="card-001",
        provenance_state="derived",
        derived_type="summary",
        parent_refs=["card-original"],
        source_refs=["fixture://source"],
        generation_basis_ref="fixture://basis",
        producer_version="producer-v1",
        lineage_refs=["fixture://lineage"],
        penalty_policy=_penalty_policy(),
        design_prior=0.8,
        base_penalty=0.8,
        override={
            "who": "user",
            "when": "2026-08-03T00:00:00Z",
            "item": "card-001",
            "old": 0.8,
            "new": 0.7,
            "reason": "frozen fixture",
            "event_locator": "RUNTIME_LOG:fixture-event-1",
            "event_hash": _hash("override-event"),
        },
        effective_penalty=0.7,
        authority_blocked=False,
        issue_codes=[],
    )
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_derived_penalty_record(record)
    assert validate_derived_penalty_record(record, penalty_policy=_penalty_policy()) == record
    applied = apply_authority_penalty(
        relevance_subtotal=0.4,
        authority_subtotal=0.2,
        penalty_record=record,
        penalty_policy=_penalty_policy(),
    )
    assert applied["relevance_subtotal"] == 0.4
    assert abs(applied["penalized_authority_subtotal"] - 0.14) < 1e-12
    attacker_policy = deepcopy(_penalty_policy())
    attacker_policy["type_penalties"]["summary"] = 1.0
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_derived_penalty_against_policy(record, attacker_policy)
    bad_event = deepcopy(_penalty_policy())
    bad_event["override_event_allowlist"][0]["event_hash"] = _hash("other-event")
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_derived_penalty_against_policy(record, bad_event)
    with unittest.TestCase().assertRaises(ContractViolation):
        build_derived_penalty_record(
            candidate_id="card-001",
            provenance_state="derived",
            derived_type="summary",
            parent_refs=["card-original"],
            source_refs=["fixture://source"],
            generation_basis_ref="fixture://basis",
            producer_version="producer-v1",
            lineage_refs=["fixture://lineage"],
            penalty_policy=_penalty_policy(),
            design_prior=1.0,
            base_penalty=1.0,
            override=None,
            effective_penalty=1.0,
            authority_blocked=False,
            issue_codes=[],
        )


def test_retrieval_policy_is_structural_only_and_hash_bound() -> None:
    snapshot = _snapshot()
    assert validate_retrieval_policy_snapshot(snapshot) == snapshot
    activated = deepcopy(snapshot)
    activated["activation_authorized"] = True
    activated = make_hashed_payload({key: value for key, value in activated.items() if key != "content_hash"})
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_retrieval_policy_snapshot(activated)
    leaked = deepcopy(snapshot)
    leaked["query_policy"]["gold_hash"] = _hash("forbidden")
    leaked = make_hashed_payload({key: value for key, value in leaked.items() if key != "content_hash"})
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_retrieval_policy_snapshot(leaked)
    query_drift = deepcopy(snapshot)
    query_drift["query_policy"]["query_set_hash"] = _hash("different-query-pack")
    query_drift = make_hashed_payload(
        {key: value for key, value in query_drift.items() if key != "content_hash"}
    )
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_retrieval_policy_snapshot(query_drift)
    threshold_drift = deepcopy(snapshot)
    threshold_drift["threshold_policy"]["function_hash"] = _hash("different-threshold")
    threshold_drift = make_hashed_payload(
        {key: value for key, value in threshold_drift.items() if key != "content_hash"}
    )
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_retrieval_policy_snapshot(threshold_drift)
    weight_drift = deepcopy(snapshot)
    weight_drift["provider_policy"]["weights"]["Similarity"] = 0.44
    weight_drift["provider_policy"]["weights"]["Semantic"] = 0.21
    assert abs(sum(weight_drift["provider_policy"]["weights"].values()) - 1.0) < 1e-12
    weight_drift = make_hashed_payload(
        {key: item for key, item in weight_drift.items() if key != "content_hash"}
    )
    with unittest.TestCase().assertRaises(ContractViolation):
        validate_retrieval_policy_snapshot(weight_drift)
    for path, value in (
        (("aggregation_policy", "baseline_policy_id"), "attacker-baseline"),
        (("aggregation_policy", "missing_data_policy"), "zero_fill"),
        (("aggregation_policy", "authority_mass_cap"), 0.34),
        (("aggregation_policy", "rrf_k"), 61),
        (("aggregation_policy", "mmr_lambda"), 0.6),
        (("aggregation_policy", "tie_break"), "score_only"),
    ):
        drift = deepcopy(snapshot)
        cursor = drift
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        drift = make_hashed_payload(
            {key: item for key, item in drift.items() if key != "content_hash"}
        )
        with unittest.TestCase().assertRaises(ContractViolation):
            validate_retrieval_policy_snapshot(drift)


class TestP03T04ShadowContracts(unittest.TestCase):
    def test_query_profile(self) -> None:
        test_query_profile_is_deterministic_and_hash_bound()

    def test_provider_set(self) -> None:
        test_provider_set_requires_exact_six_and_four_state_null_semantics()

    def test_query_registry_binding(self) -> None:
        test_query_profile_rejects_unknown_field_and_registry_hash_drift()

    def test_recursive_oracle_fields(self) -> None:
        test_recursive_oracle_and_gold_fields_are_rejected()

    def test_strict_provider_adapters(self) -> None:
        test_strict_provider_adapters_fail_closed_by_source_type()

    def test_unknown_provenance(self) -> None:
        test_unknown_provenance_blocks_authority_but_preserves_relevance()

    def test_incomplete_override(self) -> None:
        test_override_is_fail_closed_without_every_evidence_field()

    def test_complete_override(self) -> None:
        test_complete_override_and_authority_only_application()

    def test_retrieval_policy(self) -> None:
        test_retrieval_policy_is_structural_only_and_hash_bound()
