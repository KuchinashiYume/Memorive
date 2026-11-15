from __future__ import annotations

import copy
import math
import unittest

from retrieval_weighting.aggregation import AUTHORITY_MASS, PROVIDER_WEIGHTS, aggregate_availability_aware, aggregate_both_policies, aggregate_architecture_baseline, rank_score_explanations
from retrieval_weighting.derived_penalty import UNKNOWN_PROVENANCE_AUTHORITY_BLOCKED, build_derived_penalty_record
from retrieval_weighting.errors import ContractViolation
from retrieval_weighting.providers import PROVIDERS, build_absent_provider_score_record, build_strict_provider_score_record
from tests.modules.retrieval_weighting.test_shadow_contracts import (
    _penalty_policy,
    _provider as _contract_provider,
    _snapshot,
)


DEFAULT_SCORES = {
    "Similarity": 0.8,
    "Semantic": 0.6,
    "Manual": 0.9,
    "Rule": 0.7,
    "Note": 0.5,
    "Citation": 0.3,
}
SNAPSHOT = _snapshot()


def _baseline(records: list[dict], penalty: dict) -> dict:
    return aggregate_architecture_baseline(records, penalty, policy_snapshot=SNAPSHOT)


def _shadow(records: list[dict], penalty: dict) -> dict:
    return aggregate_availability_aware(records, penalty, policy_snapshot=SNAPSHOT)


def _both(records: list[dict], penalty: dict) -> dict:
    return aggregate_both_policies(records, penalty, policy_snapshot=SNAPSHOT)


def _records(
    candidate_id: str = "card-001",
    *,
    scores: dict[str, float] | None = None,
    states: dict[str, str] | None = None,
) -> list[dict]:
    values = dict(DEFAULT_SCORES)
    values.update(scores or {})
    provider_states = {provider: "observed" for provider in PROVIDERS}
    provider_states.update(states or {})
    result: list[dict] = []
    for provider in PROVIDERS:
        state = provider_states[provider]
        observed = state == "observed"
        template = _contract_provider(provider, state=state)
        if observed:
            result.append(
                build_strict_provider_score_record(
                    candidate_id=candidate_id,
                    provider=provider,
                    score=values[provider],
                    state="observed",
                    reason_code="FIXTURE_OBSERVED",
                    evidence=template["adapter_evidence"],
                    provider_version=template["provider_version"],
                )
            )
        else:
            result.append(
                build_absent_provider_score_record(
                    candidate_id=candidate_id,
                    provider=provider,
                    state=state,
                    reason_code=f"FIXTURE_{state.upper()}",
                    provider_version=template["provider_version"],
                )
            )
    return result


def _penalty(candidate_id: str = "card-001", value: float = 1.0) -> dict:
    if value == 1.0:
        return build_derived_penalty_record(
            candidate_id=candidate_id,
            provenance_state="original",
            derived_type=None,
            parent_refs=[],
            source_refs=["sandbox:source"],
            generation_basis_ref=None,
            producer_version=None,
            lineage_refs=["sandbox:lineage"],
            penalty_policy=_penalty_policy(),
            design_prior=1.0,
            base_penalty=1.0,
            override=None,
            effective_penalty=1.0,
            authority_blocked=False,
            issue_codes=[],
        )
    return build_derived_penalty_record(
        candidate_id=candidate_id,
        provenance_state="derived",
        derived_type="summary" if value == 0.8 else "half",
        parent_refs=["sandbox:parent"],
        source_refs=["sandbox:source"],
        generation_basis_ref="sandbox:generation-basis",
        producer_version="fixture-producer-v1",
        lineage_refs=["sandbox:lineage"],
        penalty_policy=_penalty_policy(),
        design_prior=value,
        base_penalty=value,
        override=None,
        effective_penalty=value,
        authority_blocked=False,
        issue_codes=[],
    )


def _unknown_penalty(candidate_id: str = "card-001") -> dict:
    return build_derived_penalty_record(
        candidate_id=candidate_id,
        provenance_state="unknown",
        derived_type=None,
        parent_refs=[],
        source_refs=[],
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


class AggregationTests(unittest.TestCase):
    def test_baseline_formula_is_exact_six_provider_replay(self) -> None:
        result = _baseline(_records(), _penalty(value=0.8))
        expected_relevance = math.fsum((0.45 * 0.8, 0.20 * 0.6))
        expected_authority = math.fsum(
            (0.15 * 0.9, 0.10 * 0.7, 0.07 * 0.5, 0.03 * 0.3)
        )
        self.assertEqual(result["policy_id"], "policy_baseline_v6_1")
        self.assertAlmostEqual(result["relevance_subtotal"], expected_relevance)
        self.assertAlmostEqual(
            result["authority_subtotal_before_penalty"], expected_authority
        )
        self.assertAlmostEqual(
            result["final_score"], expected_relevance + expected_authority * 0.8
        )
        self.assertEqual(
            [row["provider"] for row in result["provider_contributions"]],
            list(PROVIDERS),
        )

    def test_availability_policy_degenerates_to_the_baseline_when_all_six_are_observed(self) -> None:
        both = _both(_records(), _penalty(value=0.8))
        self.assertAlmostEqual(
            both["availability_aware"]["lambda_authority"], AUTHORITY_MASS
        )
        self.assertAlmostEqual(
            both["availability_aware"]["final_score"], both["baseline"]["final_score"]
        )
        self.assertEqual(both["availability_aware"]["authority_coverage"], 1.0)
        self.assertEqual(both["availability_aware"]["relevance_coverage"], 1.0)

    def test_all_authority_missing_sets_lambda_zero_and_returns_mass_to_relevance(self) -> None:
        states = {
            provider: "missing" for provider in ("Manual", "Rule", "Note", "Citation")
        }
        result = _shadow(_records(states=states), _penalty(value=0.5))
        expected_relevance_average = math.fsum((0.45 * 0.8, 0.20 * 0.6)) / 0.65
        self.assertEqual(result["lambda_authority"], 0.0)
        self.assertEqual(result["authority_coverage"], 0.0)
        self.assertEqual(result["authority_subtotal_before_penalty"], 0.0)
        self.assertAlmostEqual(result["final_score"], expected_relevance_average)

    def test_derived_penalty_changes_authority_only_never_relevance(self) -> None:
        full = _shadow(_records(), _penalty(value=1.0))
        penalized = _shadow(_records(), _penalty(value=0.5))
        self.assertEqual(penalized["relevance_subtotal"], full["relevance_subtotal"])
        self.assertEqual(
            penalized["authority_subtotal_before_penalty"],
            full["authority_subtotal_before_penalty"],
        )
        self.assertAlmostEqual(
            penalized["authority_subtotal_after_penalty"],
            full["authority_subtotal_after_penalty"] * 0.5,
        )
        relevance_rows = penalized["provider_contributions"][:2]
        self.assertTrue(all(row["penalty_applied"] is False for row in relevance_rows))

    def test_unknown_provenance_blocks_authority_but_preserves_relevance(self) -> None:
        result = _shadow(_records(), _unknown_penalty())
        expected_relevance_average = math.fsum((0.45 * 0.8, 0.20 * 0.6)) / 0.65
        self.assertEqual(result["lambda_authority"], 0.0)
        self.assertEqual(result["authority_coverage"], 0.0)
        self.assertAlmostEqual(result["authority_blocked_mass"], 0.35)
        self.assertAlmostEqual(result["final_score"], expected_relevance_average)

    def test_relevance_all_missing_blocks_candidate_ranking(self) -> None:
        states = {"Similarity": "missing", "Semantic": "not_applicable"}
        with self.assertRaisesRegex(ContractViolation, "all relevance providers"):
            _shadow(_records(states=states), _penalty())
        with self.assertRaisesRegex(ContractViolation, "all relevance providers"):
            _baseline(_records(states=states), _penalty())

    def test_provider_set_must_be_exactly_six_without_duplicate_or_seventh(self) -> None:
        records = _records()
        with self.assertRaisesRegex(ContractViolation, "exactly six"):
            _shadow(records[:-1], _penalty())
        duplicate = records[:-1] + [records[0]]
        with self.assertRaisesRegex(ContractViolation, "duplicate provider"):
            _shadow(duplicate, _penalty())

    def test_invalid_provider_is_not_silently_converted_to_zero_or_missing(self) -> None:
        records = _records(states={"Rule": "invalid"})
        with self.assertRaisesRegex(ContractViolation, "invalid provider blocks"):
            _shadow(records, _penalty())

    def test_missing_relevance_is_renormalized_without_becoming_negative_quality(self) -> None:
        records = _records(states={"Semantic": "missing"})
        baseline = _baseline(records, _penalty())
        shadow = _shadow(records, _penalty())
        self.assertAlmostEqual(baseline["relevance_subtotal"], 0.45 * 0.8)
        self.assertAlmostEqual(shadow["relevance_average"], 0.8)
        semantic_state = next(
            row for row in shadow["provider_states"] if row["provider"] == "Semantic"
        )
        self.assertEqual(semantic_state["state"], "missing")
        self.assertFalse(semantic_state["usable_in_aggregation"])

    def test_provider_contributions_recompose_with_math_fsum(self) -> None:
        result = _shadow(
            _records(scores={provider: 0.1 for provider in PROVIDERS}),
            _penalty(value=0.8),
        )
        recomposed = math.fsum(
            row["final_contribution"] for row in result["provider_contributions"]
        )
        self.assertEqual(result["final_score"], recomposed)

    def test_rank_tie_break_is_candidate_id_and_policy_mixing_is_blocked(self) -> None:
        ranked = rank_score_explanations(
            [
                {"candidate_id": "b", "policy_id": "policy_shadow_availability_aware_v1", "retrieval_policy_hash": SNAPSHOT["content_hash"], "final_score": 0.5},
                {"candidate_id": "a", "policy_id": "policy_shadow_availability_aware_v1", "retrieval_policy_hash": SNAPSHOT["content_hash"], "final_score": 0.5},
                {"candidate_id": "c", "policy_id": "policy_shadow_availability_aware_v1", "retrieval_policy_hash": SNAPSHOT["content_hash"], "final_score": 0.7},
            ],
            policy_snapshot=SNAPSHOT,
        )
        self.assertEqual(
            [(row["candidate_id"], row["rank"]) for row in ranked],
            [("c", 1), ("a", 2), ("b", 3)],
        )
        with self.assertRaisesRegex(ContractViolation, "may not mix"):
            rank_score_explanations(
                [
                    {"candidate_id": "a", "policy_id": "policy_baseline_v6_1", "retrieval_policy_hash": SNAPSHOT["content_hash"], "final_score": 0.5},
                    {"candidate_id": "b", "policy_id": "policy_shadow_availability_aware_v1", "retrieval_policy_hash": SNAPSHOT["content_hash"], "final_score": 0.4},
                ],
                policy_snapshot=SNAPSHOT,
            )

    def test_frozen_provider_weights_are_exactly_six_and_sum_to_one(self) -> None:
        self.assertEqual(tuple(PROVIDER_WEIGHTS), PROVIDERS)
        self.assertEqual(math.fsum(PROVIDER_WEIGHTS.values()), 1.0)

    def test_snapshot_must_carry_the_weights_and_authority_mass_the_formulas_use(self) -> None:
        weights = copy.deepcopy(SNAPSHOT)
        weights["provider_policy"]["weights"]["Similarity"] = 0.44
        weights["provider_policy"]["weights"]["Semantic"] = 0.21
        mass = copy.deepcopy(SNAPSHOT)
        mass["aggregation_policy"]["authority_mass_cap"] = 0.34
        for snapshot, message in ((weights, "provider weights differ"), (mass, "authority mass differs")):
            with self.assertRaisesRegex(ContractViolation, message):
                aggregate_availability_aware(_records(), _penalty(), policy_snapshot=snapshot)


if __name__ == "__main__":
    unittest.main()
