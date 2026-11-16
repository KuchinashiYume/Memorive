from __future__ import annotations

from copy import deepcopy
import unittest

from memorive.research.ranking.contracts import make_hashed_payload
from memorive.research.ranking.errors import ContractViolation, FieldVectorError
from memorive.research.ranking.field_search import search_field_vectors
from tests.modules.retrieval_weighting.current_field_records import field_records


class FieldSearchTests(unittest.TestCase):
    def test_exact_fixture_vector_returns_bound_field_and_source(self) -> None:
        records = field_records()
        target = next(row for row in records if row["field_path"] == "method/0")
        result = search_field_vectors(
            records,
            query_vector=target["embedding"],
            allowed_paper_ids=["FixtureSelf001"],
            field_path_templates=["method/{item}"],
            top_k=1,
        )
        self.assertEqual(result["hits"][0]["field_path"], "method/0")
        self.assertEqual(result["hits"][0]["field_source_id"], target["field_source_id"])
        self.assertEqual(result["hits"][0]["provenance_state"], "original")
        self.assertAlmostEqual(result["hits"][0]["score"], 1.0)

    def test_scope_and_registry_template_are_hard_filters_with_underfill(self) -> None:
        records = field_records()
        result = search_field_vectors(
            records,
            query_vector=records[0]["embedding"],
            allowed_paper_ids=["FixtureOutside999"],
            field_path_templates=["comparison_context/groups/{item}"],
            top_k=3,
        )
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["underfill_count"], 3)
        self.assertIn(
            "OUT_OF_EXPLICIT_SCOPE",
            {row["reason_code"] for row in result["rejection_counts"]},
        )

    def test_dimension_and_profile_drift_fail_closed(self) -> None:
        records = field_records()
        with self.assertRaisesRegex(ContractViolation, "dimension"):
            search_field_vectors(
                records,
                query_vector=[1.0, 0.0],
                allowed_paper_ids=["FixtureSelf001"],
                field_path_templates=["method/{item}"],
                top_k=1,
            )
        drift = deepcopy(records)
        payload = {key: value for key, value in drift[1].items() if key != "content_hash"}
        payload["embedding_profile_id"] = "offline:other-v1-d4"
        drift[1] = make_hashed_payload(payload)
        with self.assertRaises(FieldVectorError):
            search_field_vectors(
                drift,
                query_vector=records[0]["embedding"],
                allowed_paper_ids=["FixtureSelf001"],
                field_path_templates=["comparison_context/groups/{item}"],
                top_k=1,
            )

    def test_non_l2_or_unknown_template_fails_closed(self) -> None:
        records = field_records()
        with self.assertRaisesRegex(ContractViolation, "L2-normalized"):
            search_field_vectors(
                records,
                query_vector=[1.0, 1.0, 0.0, 0.0],
                allowed_paper_ids=["FixtureSelf001"],
                field_path_templates=["method/{item}"],
                top_k=1,
            )
        with self.assertRaisesRegex(ContractViolation, "unsupported placeholders"):
            search_field_vectors(
                records,
                query_vector=records[0]["embedding"],
                allowed_paper_ids=["FixtureSelf001"],
                field_path_templates=["method/{unknown}"],
                top_k=1,
            )

    def test_same_input_is_byte_stable_as_a_json_domain_value(self) -> None:
        records = field_records()
        kwargs = {
            "query_vector": records[0]["embedding"],
            "allowed_paper_ids": ["FixtureSelf001"],
            "field_path_templates": ["comparison_context/groups/{item}"],
            "top_k": 2,
        }
        first = search_field_vectors(records, **kwargs)
        second = search_field_vectors(records, **kwargs)
        self.assertEqual(first, second)

    def test_field_templates_decide_which_fields_compete(self) -> None:
        records = field_records()
        target = next(row for row in records if row["field_path"] == "method/0")
        result = search_field_vectors(
            records,
            query_vector=target["embedding"],
            allowed_paper_ids=["FixtureSelf001"],
            field_path_templates=["research_question"],
            top_k=1,
        )
        self.assertEqual([hit["field_path"] for hit in result["hits"]], ["research_question"])

    def test_hits_are_ranked_best_first(self) -> None:
        records = field_records()
        target = next(row for row in records if row["field_path"] == "method/0")
        result = search_field_vectors(
            records,
            query_vector=target["embedding"],
            allowed_paper_ids=["FixtureSelf001"],
            field_path_templates=sorted({row["field_path"] for row in records}),
            top_k=5,
        )
        scores = [hit["score"] for hit in result["hits"]]
        self.assertEqual(result["hits"][0]["field_path"], "method/0")
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([hit["rank"] for hit in result["hits"]], [1, 2, 3, 4, 5])

    def test_records_built_under_another_profile_are_not_mixed_in(self) -> None:
        records = field_records()
        other = field_records(profile_id="offline:other-v1-d4")
        with self.assertRaisesRegex(ContractViolation, "mixes frozen profiles"):
            search_field_vectors(
                [*records, other[0]],
                query_vector=records[0]["embedding"],
                allowed_paper_ids=["FixtureSelf001"],
                field_path_templates=["method/{item}"],
                top_k=1,
            )


if __name__ == "__main__":
    unittest.main()
