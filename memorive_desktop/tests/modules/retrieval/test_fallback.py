from __future__ import annotations

import unittest

from retrieval.retrieval_calibration.fallback import apply_fallback
from retrieval.retrieval_calibration.pre_rerank_snapshot import create_snapshot
ROWS = [
    {"candidate_id": "A", "paper_id": "P", "chunk_id": "A", "text": "a", "distance": 0.1},
    {"candidate_id": "B", "paper_id": "P", "chunk_id": "B", "text": "b", "distance": 0.2},
]


class FallbackTests(unittest.TestCase):
    def test_timeout_restores_exact_snapshot(self) -> None:
        snapshot = create_snapshot("Q", ROWS, profile="RERANK_ONLY_SHADOW")
        result = apply_fallback("TIMEOUT", snapshot, ROWS)
        self.assertEqual([row["candidate_id"] for row in result["result"]], ["A", "B"])
        self.assertEqual(result["receipt"]["status"], "DEGRADED_EXACT_SNAPSHOT")
        self.assertFalse(result["receipt"]["cloud_fallback_started"])

    def test_snapshot_drift_fails_closed(self) -> None:
        snapshot = create_snapshot("Q", ROWS, profile="RERANK_ONLY_SHADOW")
        drifted = [dict(ROWS[0], text="changed"), ROWS[1]]
        result = apply_fallback("OOM", snapshot, drifted)
        self.assertIsNone(result["result"])
        self.assertEqual(result["receipt"]["error_code"], "SNAPSHOT_MISMATCH")

    def test_cloud_fallback_is_forbidden_even_with_valid_snapshot(self) -> None:
        snapshot = create_snapshot("Q", ROWS, profile="RERANK_ONLY_SHADOW")
        result = apply_fallback("MODEL_MISSING", snapshot, ROWS, cloud_requested=True)
        self.assertIsNone(result["result"])
        self.assertEqual(result["receipt"]["error_code"], "CLOUD_FALLBACK_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
