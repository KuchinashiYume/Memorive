from __future__ import annotations

import unittest

from retrieval.retrieval_calibration.candidate_union_adapter import stable_candidate_union
def row(candidate_id: str, paper_id: str, distance: float = 0.1, field_score: float = 1.0):
    return {"candidate_id": candidate_id, "paper_id": paper_id, "chunk_id": candidate_id, "text": candidate_id, "distance": distance, "field_score": field_score}


class CandidateUnionTests(unittest.TestCase):
    def test_field_route_adds_missing_candidate_and_deduplicates(self) -> None:
        result = stable_candidate_union(
            [row("A", "P"), row("B", "P", 0.2)],
            [row("B", "P"), row("C", "P")],
            allowed_paper_ids=["P"],
            scope="single_paper",
            field_expected_min=1,
        )
        self.assertEqual([item["candidate_id"] for item in result["candidates"]], ["A", "B", "C"])
        self.assertEqual(set(result["candidates"][1]["channels"]), {"distance", "field"})

    def test_scope_leaks_are_removed_before_rerank(self) -> None:
        result = stable_candidate_union(
            [row("A", "P"), row("X", "FOREIGN")],
            None,
            allowed_paper_ids=["P"],
            scope="single_paper",
        )
        self.assertEqual([item["candidate_id"] for item in result["candidates"]], ["A"])
        self.assertEqual(result["receipt"]["scope_rejection_count"], 1)
        self.assertEqual(result["receipt"]["underfill_reason"], "FIELD_ROUTE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
