from __future__ import annotations

import unittest
from copy import deepcopy

from memorive.research.ranking.candidate_union import build_unified_candidate_pool, make_candidate_id, validate_candidate_identity
from memorive.research.ranking.counterevidence import COUNTEREVIDENCE_FIELD_PATH_PATTERNS, make_frozen_distance_matrix, select_counterevidence_candidates, validate_frozen_distance_matrix
from memorive.research.ranking.contracts import make_hashed_payload
from memorive.research.ranking.errors import ContractViolation, IdentityConflict, SourceSnapshotDriftError
from tests.modules.retrieval_weighting.test_shadow_contracts import _snapshot


def _identity(
    suffix: str,
    *,
    paper_id: str = "paper-a",
    card_id: str | None = None,
    unit_kind: str = "chunk",
) -> dict[str, object]:
    return {
        "paper_id": paper_id,
        "card_id": card_id or f"card-{suffix}",
        "card_revision": 1,
        "unit_kind": unit_kind,
        "unit_id": f"unit-{suffix}",
        "source_id": f"source-{suffix}",
    }


def _hit(
    suffix: str,
    rank: int,
    *,
    paper_id: str = "paper-a",
    card_id: str | None = None,
    active: bool = True,
    rights_allowed: bool = True,
    raw_score: float = 0.5,
    text: str = "same text is deliberately irrelevant to identity",
) -> dict[str, object]:
    return {
        "identity": _identity(suffix, paper_id=paper_id, card_id=card_id),
        "rank": rank,
        "raw_score": raw_score,
        "score_profile": "fixture:ranked-v1",
        "active": active,
        "rights_allowed": rights_allowed,
        "source_family": f"family-{suffix}",
        "doc_type": "article",
        "text": text,
    }


def _pool(channels: dict[str, list[dict[str, object]]], **overrides: object) -> dict:
    options = {
        "allowed_paper_ids": ["paper-a"],
        "pool_size": 10,
        "rrf_k": 60,
        "channel_cap": 10,
        "per_card_cap": 3,
    }
    options.update(overrides)
    snapshot = deepcopy(_snapshot())
    snapshot.pop("content_hash")
    policy = snapshot["aggregation_policy"]
    policy["top_k"] = options.pop("pool_size")
    policy["rrf_k"] = options.pop("rrf_k")
    channel_cap = options.pop("channel_cap")
    policy["channel_caps"] = {
        channel: channel_cap for channel in sorted(channels)
    }
    policy["per_card_cap"] = options.pop("per_card_cap")
    return build_unified_candidate_pool(
        channels,
        policy_snapshot=make_hashed_payload(snapshot),
        **options,
    )


def _counter_policy(
    *,
    slots: int,
    minimum_relevance: float,
    per_source_cap: int,
    per_paper_cap: int,
) -> dict:
    snapshot = deepcopy(_snapshot())
    snapshot.pop("content_hash")
    policy = snapshot["aggregation_policy"]
    policy["counterevidence_slots"] = slots
    policy["counter_minimum_relevance"] = minimum_relevance
    policy["counter_per_source_cap"] = per_source_cap
    policy["counter_per_paper_cap"] = per_paper_cap
    return make_hashed_payload(snapshot)


def _counter_candidate(
    candidate_id: str,
    relevance: float,
    *,
    paper_id: str = "paper-a",
    source_family: str | None = None,
    active: bool = True,
    scope_eligible: bool = True,
    rights_allowed: bool = True,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "paper_id": paper_id,
        "card_id": f"card-{candidate_id}",
        "source_id": f"source-{candidate_id}",
        "source_family": source_family or f"family-{candidate_id}",
        "counter_relevance": relevance,
        "active": active,
        "scope_eligible": scope_eligible,
        "rights_allowed": rights_allowed,
        "field_id": "author_limitations_outlook/limitations/0/quote",
    }


def _matrix(ids: list[str], default_distance: float = 1.0) -> dict[str, dict[str, float]]:
    return {
        left: {
            right: 0.0 if left == right else default_distance
            for right in ids
        }
        for left in ids
    }


class CandidateUnionAndCounterevidenceTests(unittest.TestCase):
    def test_rrf_is_rank_only_pool_metadata_and_not_a_provider(self) -> None:
        shared = _hit("shared", 1, raw_score=-999.0)
        pool = _pool(
            {
                "field:limitations": [shared, _hit("field-only", 2, raw_score=999.0)],
                "legacy": [_hit("shared", 1, raw_score=0.0), _hit("legacy-only", 2)],
            }
        )
        self.assertEqual(pool["fusion_method"], "rank_only_rrf_v1")
        self.assertFalse(pool["rrf_is_provider"])
        self.assertEqual(
            pool["candidates"][0]["candidate_id"], make_candidate_id(_identity("shared"))
        )
        self.assertAlmostEqual(pool["candidates"][0]["pool_rrf_score"], 2 / 61)
        self.assertNotIn("provider", pool["candidates"][0])
        self.assertEqual(
            [hit["raw_score"] for hit in pool["candidates"][0]["channel_hits"]],
            [-999.0, 0.0],
        )

    def test_raw_scores_cannot_change_rrf_order_or_score(self) -> None:
        first = _pool(
            {"legacy": [_hit("a", 1, raw_score=-100), _hit("b", 2, raw_score=100)]}
        )
        second = _pool(
            {"legacy": [_hit("a", 1, raw_score=100), _hit("b", 2, raw_score=-100)]}
        )
        self.assertEqual(
            [row["candidate_id"] for row in first["candidates"]],
            [row["candidate_id"] for row in second["candidates"]],
        )
        self.assertEqual(
            [row["pool_rrf_score"] for row in first["candidates"]],
            [row["pool_rrf_score"] for row in second["candidates"]],
        )

    def test_active_scope_and_rights_are_hard_filtered_with_honest_underfill(self) -> None:
        pool = _pool(
            {
                "legacy": [
                    _hit("inactive", 1, active=False),
                    _hit("outside", 2, paper_id="paper-b"),
                    _hit("rights", 3, rights_allowed=False),
                    _hit("ok", 4),
                ]
            },
            pool_size=4,
        )
        self.assertEqual([row["card_id"] for row in pool["candidates"]], ["card-ok"])
        self.assertTrue(pool["underfilled"])
        self.assertEqual(pool["underfill_count"], 3)
        counts = {
            row["reason_code"]: row["count"]
            for row in pool["hard_filter_report"]["rejection_counts"]
        }
        self.assertEqual(
            counts,
            {"INACTIVE": 1, "OUT_OF_EXPLICIT_SCOPE": 1, "RIGHTS_NOT_ALLOWED": 1},
        )

    def test_unknown_active_state_fails_closed_instead_of_becoming_missing(self) -> None:
        hit = _hit("a", 1)
        hit["active"] = "active"
        with self.assertRaisesRegex(ContractViolation, "explicit boolean"):
            _pool({"legacy": [hit]})

    def test_identity_exact_set_rejects_text_and_same_text_does_not_dedupe(self) -> None:
        invalid = _identity("a")
        invalid["text"] = "identity must not include text"
        with self.assertRaisesRegex(ContractViolation, "non-text exact set"):
            validate_candidate_identity(invalid)
        pool = _pool(
            {"legacy": [_hit("a", 1, text="identical"), _hit("b", 2, text="identical")]}
        )
        self.assertEqual(pool["populated_count"], 2)
        self.assertEqual(len({row["candidate_id"] for row in pool["candidates"]}), 2)

    def test_duplicate_identity_in_one_channel_is_not_silently_deduplicated(self) -> None:
        with self.assertRaisesRegex(IdentityConflict, "duplicate structured"):
            _pool({"legacy": [_hit("a", 1), _hit("a", 2)]})

    def test_per_card_cap_causes_reported_underfill_not_cross_boundary_fill(self) -> None:
        pool = _pool(
            {
                "legacy": [
                    _hit("a1", 1, card_id="card-a"),
                    _hit("a2", 2, card_id="card-a"),
                    _hit("a3", 3, card_id="card-a"),
                ]
            },
            pool_size=3,
            per_card_cap=1,
        )
        self.assertEqual(pool["populated_count"], 1)
        self.assertEqual(pool["underfill_count"], 2)
        reasons = {row["reason_code"] for row in pool["underfill_reasons"]}
        self.assertTrue(
            {"ELIGIBLE_POOL_EXHAUSTED", "PER_CARD_CAP_EXHAUSTION"}.issubset(reasons)
        )

    def test_mmr_consumes_frozen_distance_matrix_and_prefers_diversity(self) -> None:
        candidates = [
            _counter_candidate("a", 0.90),
            _counter_candidate("a-near", 0.88),
            _counter_candidate("b-far", 0.80),
        ]
        distances = _matrix(["a", "a-near", "b-far"])
        distances["a"]["a-near"] = distances["a-near"]["a"] = 0.0
        frozen = make_frozen_distance_matrix(
            distances, [row["candidate_id"] for row in candidates]
        )
        result = select_counterevidence_candidates(
            candidates,
            allowed_paper_ids=["paper-a"],
            policy_snapshot=_counter_policy(
                slots=2,
                minimum_relevance=0.5,
                per_source_cap=1,
                per_paper_cap=3,
            ),
            frozen_distance_matrix=frozen,
            expected_matrix_sha256=frozen["matrix_sha256"],
        )
        self.assertEqual(
            [row["candidate_id"] for row in result["candidates"]], ["a", "b-far"]
        )
        self.assertTrue(
            all(row["counterevidence_candidate"] for row in result["candidates"])
        )
        self.assertEqual(
            result["claim_status"], "candidate_only_not_scientific_conclusion"
        )

    def test_distance_matrix_mutation_after_freeze_is_blocked(self) -> None:
        frozen = make_frozen_distance_matrix(_matrix(["a", "b"]), ["a", "b"])
        mutated = deepcopy(frozen)
        mutated["distances"]["a"]["b"] = 0.25
        mutated["distances"]["b"]["a"] = 0.25
        with self.assertRaisesRegex(SourceSnapshotDriftError, "hash drift"):
            validate_frozen_distance_matrix(mutated, ["a", "b"])

    def test_counterevidence_source_cap_underfills_instead_of_force_filling(self) -> None:
        candidates = [
            _counter_candidate("a", 0.9, source_family="same"),
            _counter_candidate("b", 0.8, source_family="same"),
            _counter_candidate("c", 0.7, source_family="same"),
        ]
        frozen = make_frozen_distance_matrix(
            _matrix(["a", "b", "c"]), ["a", "b", "c"]
        )
        result = select_counterevidence_candidates(
            candidates,
            allowed_paper_ids=["paper-a"],
            policy_snapshot=_counter_policy(
                slots=3,
                minimum_relevance=0.5,
                per_source_cap=1,
                per_paper_cap=3,
            ),
            frozen_distance_matrix=frozen,
        )
        self.assertEqual(result["selected_count"], 1)
        self.assertTrue(result["underfilled"])
        self.assertEqual(result["underfill_count"], 2)
        self.assertIn(
            "SOURCE_FAMILY_CAP",
            {row["reason_code"] for row in result["underfill_reasons"]},
        )

    def test_counterevidence_hard_filters_cannot_be_bypassed_by_high_score(self) -> None:
        candidates = [
            _counter_candidate("inactive", 1.0, active=False),
            _counter_candidate("rights", 1.0, rights_allowed=False),
            _counter_candidate("outside", 1.0, paper_id="paper-b"),
            _counter_candidate("ok", 0.5),
        ]
        result = select_counterevidence_candidates(
            candidates,
            allowed_paper_ids=["paper-a"],
            policy_snapshot=_counter_policy(
                slots=4,
                minimum_relevance=0.1,
                per_source_cap=4,
                per_paper_cap=4,
            ),
            # Only the hard-filtered eligible ID may enter the frozen matrix.
            frozen_distance_matrix=make_frozen_distance_matrix(
                {"ok": {"ok": 0.0}}, ["ok"]
            ),
        )
        self.assertEqual([row["candidate_id"] for row in result["candidates"]], ["ok"])
        self.assertEqual(result["underfill_count"], 3)

    def test_filtered_candidates_must_not_enter_distance_matrix_exact_set(self) -> None:
        candidates = [
            _counter_candidate("inactive", 1.0, active=False),
            _counter_candidate("ok", 0.5),
        ]
        leaked = make_frozen_distance_matrix(
            _matrix(["inactive", "ok"]), ["inactive", "ok"]
        )
        with self.assertRaisesRegex(ContractViolation, "rows must match"):
            select_counterevidence_candidates(
                candidates,
                allowed_paper_ids=["paper-a"],
                policy_snapshot=_counter_policy(
                    slots=1,
                    minimum_relevance=0.1,
                    per_source_cap=1,
                    per_paper_cap=1,
                ),
                frozen_distance_matrix=leaked,
            )

    def test_counterevidence_requires_frozen_independent_field_paths(self) -> None:
        self.assertEqual(
            COUNTEREVIDENCE_FIELD_PATH_PATTERNS,
            (
                r"author_conclusion",
                r"boundary_conditions/[0-9]+",
                r"author_limitations_outlook/limitations/[0-9]+/quote",
                r"author_limitations_outlook/outlook/[0-9]+/quote",
            ),
        )
        invalid = _counter_candidate("invalid", 0.9)
        invalid["field_id"] = "method/0"
        frozen = make_frozen_distance_matrix(
            {"invalid": {"invalid": 0.0}}, ["invalid"]
        )
        with self.assertRaisesRegex(ContractViolation, "outside the frozen independent"):
            select_counterevidence_candidates(
                [invalid],
                allowed_paper_ids=["paper-a"],
                policy_snapshot=_counter_policy(
                    slots=1,
                    minimum_relevance=0.1,
                    per_source_cap=1,
                    per_paper_cap=1,
                ),
                frozen_distance_matrix=frozen,
            )

        missing = _counter_candidate("missing", 0.9)
        del missing["field_id"]
        with self.assertRaisesRegex(ContractViolation, "missing required fields"):
            select_counterevidence_candidates(
                [missing],
                allowed_paper_ids=["paper-a"],
                policy_snapshot=_counter_policy(
                    slots=1,
                    minimum_relevance=0.1,
                    per_source_cap=1,
                    per_paper_cap=1,
                ),
                frozen_distance_matrix=make_frozen_distance_matrix(
                    {"missing": {"missing": 0.0}}, ["missing"]
                ),
            )

    def test_mmr_exact_tie_break_is_candidate_id(self) -> None:
        candidates = [_counter_candidate("b", 0.5), _counter_candidate("a", 0.5)]
        frozen = make_frozen_distance_matrix(_matrix(["a", "b"]), ["b", "a"])
        result = select_counterevidence_candidates(
            candidates,
            allowed_paper_ids=["paper-a"],
            policy_snapshot=_counter_policy(
                slots=2,
                minimum_relevance=0.1,
                per_source_cap=1,
                per_paper_cap=2,
            ),
            frozen_distance_matrix=frozen,
        )
        self.assertEqual(
            [row["candidate_id"] for row in result["candidates"]], ["a", "b"]
        )


if __name__ == "__main__":
    unittest.main()
