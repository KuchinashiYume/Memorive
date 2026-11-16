from __future__ import annotations

import unittest

from memorive.research.ranking.errors import ContractViolation, FieldVectorError
from memorive.research.ranking.semantic_field_search import search_semantic_field_overlays
from tests.modules.retrieval_weighting.test_semantic_field_profiles import (
    semantic_overlay,
    semantic_profile,
    source_fields,
)


class SemanticFieldSearchTests(unittest.TestCase):
    def test_semantic_ranking_is_independent_and_deterministic(self) -> None:
        kwargs = {
            "profile": semantic_profile(),
            "source_records": source_fields(),
            "query_vector": [1.0, 0.0, 0.0],
            "allowed_paper_ids": ["paper-001", "paper-002"],
            "field_path_templates": ["limitations/{item}", "method/{item}"],
            "top_k": 2,
        }
        first = search_semantic_field_overlays(semantic_overlay(), **kwargs)
        second = search_semantic_field_overlays(semantic_overlay(), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["hits"][0]["field_id"], "field-001")
        self.assertEqual(first["hits"][0]["score"], 1.0)

    def test_scope_and_field_path_are_hard_filters_with_underfill(self) -> None:
        result = search_semantic_field_overlays(
            semantic_overlay(),
            profile=semantic_profile(),
            source_records=source_fields(),
            query_vector=[1.0, 0.0, 0.0],
            allowed_paper_ids=["paper-002"],
            field_path_templates=["method/{item}"],
            top_k=2,
        )
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["underfill_count"], 2)
        self.assertEqual(
            {item["reason_code"] for item in result["rejection_counts"]},
            {"FIELD_PATH_NOT_REQUESTED", "OUT_OF_EXPLICIT_SCOPE"},
        )

    def test_query_dimension_l2_and_template_drift_fail_closed(self) -> None:
        base = {
            "profile": semantic_profile(),
            "source_records": source_fields(),
            "allowed_paper_ids": ["paper-001"],
            "field_path_templates": ["method/{item}"],
            "top_k": 1,
        }
        with self.assertRaisesRegex(FieldVectorError, "L2-normalized"):
            search_semantic_field_overlays(
                semantic_overlay(), query_vector=[1.0, 1.0, 0.0], **base
            )
        base["field_path_templates"] = ["method/{unsupported}"]
        with self.assertRaisesRegex(ContractViolation, "unsupported"):
            search_semantic_field_overlays(
                semantic_overlay(), query_vector=[1.0, 0.0, 0.0], **base
            )


if __name__ == "__main__":
    unittest.main()
