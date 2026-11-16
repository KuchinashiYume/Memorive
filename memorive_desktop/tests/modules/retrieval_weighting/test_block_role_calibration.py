from __future__ import annotations

import unittest

from memorive.research.ranking.retrieval_calibration_block_role_calibration import BlockRoleCalibrationPolicy, NEUTRAL, PROTECT, SOFT_PENALIZE
Retrieval_SHA = "B" * 64


class BlockRoleCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = BlockRoleCalibrationPolicy(retrieval_terminal_sha256=Retrieval_SHA)

    def test_ordinary_reference_is_soft_only(self) -> None:
        decision = self.policy.decide("quantitative_result", "references")
        self.assertEqual(decision.action, SOFT_PENALIZE)
        self.assertFalse(decision.hard_drop)

    def test_citation_reference_and_discussion_are_protected(self) -> None:
        self.assertEqual(
            self.policy.decide("citation_bibliographic", "references").action,
            PROTECT,
        )
        self.assertEqual(
            self.policy.decide("method_condition", "citation_discussion").action,
            PROTECT,
        )

    def test_unknown_inconsistent_and_low_confidence_are_neutral(self) -> None:
        self.assertEqual(self.policy.decide("method_condition", "unknown").action, NEUTRAL)
        self.assertEqual(
            self.policy.decide(
                "conclusion_mechanism", "references", metadata_status="inconsistent"
            ).action,
            NEUTRAL,
        )
        self.assertEqual(
            self.policy.decide(
                "conclusion_mechanism", "toc", role_confidence="low"
            ).action,
            NEUTRAL,
        )

    def test_rank_domain_calibration_keeps_every_candidate(self) -> None:
        rows = [
            {"candidate_id": "D", "base_rank": 1, "source_role": "references", "metadata_status": "vector"},
            {"candidate_id": "B", "base_rank": 2, "source_role": "body", "metadata_status": "vector"},
            {"candidate_id": "A", "base_rank": 3, "source_role": "results", "metadata_status": "vector"},
            {"candidate_id": "C", "base_rank": 4, "source_role": "citation_discussion", "metadata_status": "vector"},
        ]
        output = self.policy.calibrate("quantitative_result", rows)
        self.assertEqual([row["candidate_id"] for row in output], ["B", "A", "C", "D"])
        self.assertEqual(len(output), len(rows))
        self.assertTrue(all(row["hard_drop"] is False for row in output))


if __name__ == "__main__":
    unittest.main()
