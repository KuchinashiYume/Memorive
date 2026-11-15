from __future__ import annotations

import unittest

from retrieval_weighting.retrieval_calibration_route_profile import RetrievalProfile, resolve_route, route_matrix
class RouteProfileTests(unittest.TestCase):
    def test_off_never_enables_new_routes(self) -> None:
        result = resolve_route(
            RetrievalProfile.BASELINE_DISTANCE_OFF,
            field_route_ready=True,
            local_reranker_ready=True,
        )
        self.assertFalse(result.field_route)
        self.assertFalse(result.local_reranker)
        self.assertFalse(result.production_eligible)

    def test_underfill_keeps_distance_pool(self) -> None:
        result = resolve_route(
            RetrievalProfile.STRUCTURE_ONLY_SHADOW,
            field_route_ready=False,
            local_reranker_ready=True,
        )
        self.assertEqual(result.fallback, "DISTANCE_CANDIDATE_POOL")
        self.assertTrue(result.degraded)

    def test_missing_model_needs_exact_snapshot(self) -> None:
        safe = resolve_route(
            RetrievalProfile.RERANK_ONLY_SHADOW,
            field_route_ready=True,
            local_reranker_ready=False,
            snapshot_valid=True,
        )
        unsafe = resolve_route(
            RetrievalProfile.RERANK_ONLY_SHADOW,
            field_route_ready=True,
            local_reranker_ready=False,
            snapshot_valid=False,
        )
        self.assertEqual(safe.fallback, "EXACT_PRE_RERANK_SNAPSHOT")
        self.assertTrue(unsafe.fail_closed)

    def test_matrix_contains_exact_four_profiles_and_no_cloud(self) -> None:
        matrix = route_matrix(field_route_ready=True, local_reranker_ready=True)
        self.assertEqual(set(matrix), {profile.value for profile in RetrievalProfile})
        self.assertTrue(all(row["cloud_fallback"] is False for row in matrix.values()))


if __name__ == "__main__":
    unittest.main()
