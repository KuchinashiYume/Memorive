from __future__ import annotations

import unittest

from retrieval.retrieval_calibration.profile_compare import compare_profiles, run_profile
from retrieval_weighting.retrieval_calibration_block_role_calibration import BlockRoleCalibrationPolicy
Retrieval_SHA = "B" * 64
QUERY = {"query_id": "Q", "query_intent": "quantitative_result"}
ROWS = [
    {"candidate_id": "D", "paper_id": "P", "chunk_id": "D", "text": "noise", "distance": 0.1, "base_rank": 1, "source_role": "references", "metadata_status": "vector"},
    {"candidate_id": "B", "paper_id": "P", "chunk_id": "B", "text": "support", "distance": 0.2, "base_rank": 2, "source_role": "body", "metadata_status": "vector"},
    {"candidate_id": "A", "paper_id": "P", "chunk_id": "A", "text": "answer", "distance": 0.3, "base_rank": 3, "source_role": "results", "metadata_status": "vector"},
    {"candidate_id": "C", "paper_id": "P", "chunk_id": "C", "text": "discussion", "distance": 0.4, "base_rank": 4, "source_role": "citation_discussion", "metadata_status": "vector"},
]


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = BlockRoleCalibrationPolicy(retrieval_terminal_sha256=Retrieval_SHA)
        self.scores = [
            {"index": 0, "score": 0.01},
            {"index": 1, "score": 0.7},
            {"index": 2, "score": 0.9},
            {"index": 3, "score": 0.6},
        ]

    def test_four_profiles_and_structure_hard_negative_reduction(self) -> None:
        outputs = compare_profiles(
            QUERY,
            ROWS,
            policy=self.policy,
            rerank_only_scores=self.scores,
            combined_scores=self.scores,
        )
        self.assertEqual(len(outputs), 4)
        self.assertEqual(outputs["BASELINE_DISTANCE_OFF"]["candidate_ids"], ["D", "B", "A", "C"])
        self.assertEqual(outputs["STRUCTURE_ONLY_SHADOW"]["candidate_ids"], ["B", "A", "C", "D"])

    def test_combined_rank_domain_fusion_does_not_erase_structure_penalty(self) -> None:
        # Scores are aligned to the structure preorder B,A,C,D.  Pure rerank
        # would place D second; rank-domain fusion adds D's frozen delta.
        combined_scores = [
            {"index": 0, "score": 0.6},
            {"index": 1, "score": 0.9},
            {"index": 2, "score": 0.5},
            {"index": 3, "score": 0.7},
        ]
        output = run_profile(
            QUERY,
            ROWS,
            "STRUCTURE_PLUS_RERANK_SHADOW",
            policy=self.policy,
            score_rows=combined_scores,
        )
        self.assertEqual(output["candidate_ids"], ["A", "B", "C", "D"])
        d_row = next(row for row in output["candidates"] if row["candidate_id"] == "D")
        self.assertEqual(d_row["rerank_rank"], 2)
        self.assertEqual(d_row["rank_fusion_value"], 6)
        self.assertEqual(d_row["rank_fusion_method"], "RERANK_RANK_PLUS_STRUCTURE_DELTA")

    def test_invalid_partial_or_nonfinite_scores_reject_whole_batch(self) -> None:
        for bad in (
            [{"index": 0, "score": 0.1}],
            [{"index": 0, "score": 0.1}, {"index": 0, "score": 0.2}, {"index": 2, "score": 0.3}, {"index": 3, "score": 0.4}],
            [{"index": 0, "score": 0.1}, {"index": 1, "score": float("nan")}, {"index": 2, "score": 0.3}, {"index": 3, "score": 0.4}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_profile(QUERY, ROWS, "RERANK_ONLY_SHADOW", policy=self.policy, score_rows=bad)


if __name__ == "__main__":
    unittest.main()
