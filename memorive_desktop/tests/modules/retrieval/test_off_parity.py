from __future__ import annotations

from copy import deepcopy
import unittest

from memorive.research.retrieval.retrieval_calibration.profile_compare import run_profile
class _ForbiddenPolicy:
    def calibrate(self, *_args, **_kwargs):
        raise AssertionError("OFF_CALLED_CALIBRATION")


class OffParityTests(unittest.TestCase):
    def test_off_preserves_identity_order_distance_and_null_score(self) -> None:
        candidates = [
            {"candidate_id": "A", "paper_id": "P", "chunk_id": "A", "text": "a", "distance": 0.1, "base_rank": 1, "rerank_score": None},
            {"candidate_id": "B", "paper_id": "P", "chunk_id": "B", "text": "b", "distance": 0.2, "base_rank": 2, "rerank_score": None},
        ]
        before = deepcopy(candidates)
        output = run_profile(
            {"query_id": "Q", "query_intent": "quantitative_result"},
            candidates,
            "BASELINE_DISTANCE_OFF",
            policy=_ForbiddenPolicy(),
        )
        projection = [
            (row["candidate_id"], row["distance"], row["rerank_score"])
            for row in output["candidates"]
        ]
        self.assertEqual(projection, [("A", 0.1, None), ("B", 0.2, None)])
        self.assertEqual(candidates, before)

    def test_off_rejects_injected_scores(self) -> None:
        with self.assertRaisesRegex(ValueError, "FORBIDDEN"):
            run_profile(
                {"query_id": "Q", "query_intent": "method_condition"},
                [{"candidate_id": "A", "paper_id": "P", "chunk_id": "A", "text": "a", "distance": 0.1}],
                "BASELINE_DISTANCE_OFF",
                policy=_ForbiddenPolicy(),
                score_rows=[{"index": 0, "score": 0.5}],
            )


if __name__ == "__main__":
    unittest.main()
