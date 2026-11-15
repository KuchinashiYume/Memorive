"""quality-B red/green tests for claim-evidence binding and per-card propagation."""
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from evidence_review.claim_evidence import validate_claim_evidence_review
from evidence_review.claim_evidence import apply_fact_verdicts, build_claim_evidence_view, field_credibility_summaries, render_reviewed_field
class ClaimEvidenceContractV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.card = {
            "paper_id": "Paper",
            "schema_version": "6",
            "key_results": "Removal reached 77%. Pressure decline was 0.043 mbar/min.",
            "source_anchor": {
                "paper_id": "Paper",
                "by_field": {
                    "key_results": [
                        {"chunk_id": "Paper#c1", "quote": "Removal reached 77%."},
                        {"chunk_id": "Paper#c2", "quote": "Pressure decline was 0.043 mbar/min."},
                        {"chunk_id": "Paper#c2", "quote": "Pressure decline was 0.043 mbar/min."},
                    ]
                },
            },
        }
        self.chunks = [
            {"paper_id": "Paper", "chunk_id": "Paper#c1", "text": "Removal reached 77%."},
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c2",
                "text": "Pressure decline was 0.043 mbar/min.",
            },
        ]

    @staticmethod
    def _supported_payload(view: dict) -> dict:
        reviews = []
        for claim in view["claims"]:
            link_ids = claim["allowed_link_ids"]
            reviews.append(
                {
                    "claim_id": claim["claim_id"],
                    "atom_reviews": [
                        {
                            "atom_id": atom["atom_id"],
                            "status": "SUPPORTED",
                            "evidence_link_ids": [link_ids[0]] if link_ids else [],
                            "reason": "",
                        }
                        for atom in claim["atoms"]
                        if atom["required"]
                    ],
                }
            )
        return {"claim_reviews": reviews}

    def test_registry_deduplicates_evidence_and_maps_each_claim_locally(self) -> None:
        view = build_claim_evidence_view(
            deepcopy(self.card),
            deepcopy(self.chunks),
            fields=("key_results",),
        )

        self.assertEqual(view["schema_version"], "claim-evidence-view-v3")
        self.assertEqual(view["review_contract_id"], "card-review-claim-evidence-v3")
        self.assertEqual(len(view["claims"]), 2)
        self.assertEqual(len(view["evidence_records"]), 2)
        self.assertEqual(len(view["claim_evidence_links"]), 2)
        self.assertTrue(all(claim["mapping_status"] == "resolved" for claim in view["claims"]))
        self.assertTrue(all(len(claim["allowed_link_ids"]) == 1 for claim in view["claims"]))

        linked_chunks = {}
        links = {item["link_id"]: item for item in view["claim_evidence_links"]}
        evidence = {item["evidence_id"]: item for item in view["evidence_records"]}
        for claim in view["claims"]:
            link = links[claim["allowed_link_ids"][0]]
            linked_chunks[claim["original_text"]] = evidence[link["evidence_id"]]["chunk_id"]
        self.assertEqual(linked_chunks["Removal reached 77%."], "Paper#c1")
        self.assertEqual(
            linked_chunks["Pressure decline was 0.043 mbar/min."],
            "Paper#c2",
        )

    def test_valid_review_passes_only_with_bound_links(self) -> None:
        view = build_claim_evidence_view(self.card, self.chunks, fields=("key_results",))
        receipt = validate_claim_evidence_review(
            self._supported_payload(view),
            view,
            self.chunks,
        )
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["model_verdict"], "MODEL_REVIEW_PASS")

    def test_other_claim_link_is_rejected_even_when_chunk_is_same_paper(self) -> None:
        view = build_claim_evidence_view(self.card, self.chunks, fields=("key_results",))
        payload = self._supported_payload(view)
        wrong_link = view["claims"][1]["allowed_link_ids"][0]
        payload["claim_reviews"][0]["atom_reviews"][0]["evidence_link_ids"] = [wrong_link]

        receipt = validate_claim_evidence_review(payload, view, self.chunks)

        self.assertFalse(receipt["ok"])
        self.assertIn("EVIDENCE_LINK_CLAIM_MISMATCH", receipt["errors"])

    def test_unresolved_mapping_cannot_be_reported_as_supported(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal improved substantially."
        card["source_anchor"]["by_field"]["key_results"] = [
            {"chunk_id": "Paper#c1", "quote": "A different observation."}
        ]
        chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c1",
                "text": "A different observation.",
            }
        ]
        view = build_claim_evidence_view(card, chunks, fields=("key_results",))
        self.assertEqual(view["claims"][0]["mapping_status"], "unresolved")

        receipt = validate_claim_evidence_review(
            self._supported_payload(view),
            view,
            chunks,
        )

        self.assertFalse(receipt["ok"])
        self.assertIn("MAPPING_UNRESOLVED", receipt["errors"])

    def test_claim_matching_hallucinated_quote_cannot_resolve_against_wrong_chunk(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal reached 77%."
        card["source_anchor"]["by_field"]["key_results"] = [
            {"chunk_id": "Paper#c2", "quote": "Removal reached 77%."}
        ]
        chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c2",
                "text": "Pressure decline was 0.043 mbar/min.",
            }
        ]

        view = build_claim_evidence_view(card, chunks, fields=("key_results",))
        receipt = validate_claim_evidence_review(
            self._supported_payload(view),
            view,
            chunks,
        )

        self.assertEqual(view["claims"][0]["mapping_status"], "unresolved")
        self.assertEqual(view["claims"][0]["allowed_link_ids"], [])
        self.assertFalse(receipt["ok"])
        self.assertIn("MAPPING_UNRESOLVED", receipt["errors"])

    def test_invalid_candidate_does_not_poison_a_valid_claim_link(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal reached 77%."
        card["source_anchor"]["by_field"]["key_results"] = [
            {"chunk_id": "Paper#c2", "quote": "Removal reached 77%."},
            {"chunk_id": "Paper#c1", "quote": "Removal reached 77%."},
        ]

        view = build_claim_evidence_view(card, self.chunks, fields=("key_results",))

        claim = view["claims"][0]
        self.assertEqual(claim["mapping_status"], "resolved")
        self.assertEqual(len(claim["allowed_link_ids"]), 1)
        self.assertEqual(len(claim["candidate_link_ids"]), 1)
        receipt = validate_claim_evidence_review(
            self._supported_payload(view),
            view,
            self.chunks,
        )
        self.assertTrue(receipt["ok"])

    def test_unsupported_current_anchor_reselects_exact_same_paper_source(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal reached 77%."
        card["source_anchor"]["by_field"]["key_results"] = [
            {"chunk_id": "Paper#c2", "quote": "Pressure decline was 0.043 mbar/min."}
        ]

        view = build_claim_evidence_view(card, self.chunks, fields=("key_results",))

        claim = view["claims"][0]
        links = {
            item["link_id"]: item for item in view["claim_evidence_links"]
        }
        evidence = {
            item["evidence_id"]: item for item in view["evidence_records"]
        }
        selected = evidence[links[claim["allowed_link_ids"][0]]["evidence_id"]]
        self.assertEqual(selected["chunk_id"], "Paper#c1")
        self.assertTrue(selected["anchor_reselected"])
        self.assertEqual(claim["mapping_status"], "resolved")

    def test_fact_verdict_propagates_to_all_occurrences(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Removal reached 77%."
        card["boundary_conditions"] = ["Removal reached 77%."]
        card["source_anchor"]["by_field"]["boundary_conditions"] = [
            {"chunk_id": "Paper#c1", "quote": "Removal reached 77%."}
        ]
        view = build_claim_evidence_view(
            card,
            self.chunks,
            fields=("key_results", "boundary_conditions"),
        )
        self.assertEqual(len({claim["fact_id"] for claim in view["claims"]}), 1)
        fact_id = view["claims"][0]["fact_id"]

        updated = apply_fact_verdicts(view, {fact_id: "flagged"})

        self.assertTrue(all(item["verdict"] == "flagged" for item in updated["claims"]))
        self.assertTrue(all(item["verdict"] == "flagged" for item in updated["occurrences"]))
        summaries = field_credibility_summaries(updated)
        self.assertEqual(summaries["key_results"]["status"], "verified_with_flags")
        self.assertEqual(summaries["boundary_conditions"]["status"], "verified_with_flags")

    def test_distinct_wording_with_same_evidence_and_numeric_scope_shares_fact(self) -> None:
        card = deepcopy(self.card)
        card["key_results"] = "Sodium tolerance reached 15000 mg/L."
        card["boundary_conditions"] = [
            "The reported sodium tolerance was 15000 mg/L under tested conditions."
        ]
        quote = "Sodium tolerance reached 15000 mg/L under tested conditions."
        card["source_anchor"]["by_field"]["key_results"] = [
            {"chunk_id": "Paper#c3", "quote": quote}
        ]
        card["source_anchor"]["by_field"]["boundary_conditions"] = [
            {"chunk_id": "Paper#c3", "quote": quote}
        ]
        chunks = [
            {"paper_id": "Paper", "chunk_id": "Paper#c3", "text": quote}
        ]

        view = build_claim_evidence_view(
            card,
            chunks,
            fields=("key_results", "boundary_conditions"),
        )

        self.assertEqual(len({claim["fact_id"] for claim in view["claims"]}), 1)

    def test_removed_claim_does_not_erase_unrelated_verified_claim(self) -> None:
        view = build_claim_evidence_view(self.card, self.chunks, fields=("key_results",))
        verdicts = {
            view["claims"][0]["fact_id"]: "removed",
            view["claims"][1]["fact_id"]: "verified",
        }
        updated = apply_fact_verdicts(view, verdicts)

        rendered = render_reviewed_field(updated, "key_results")

        self.assertNotIn("Removal reached 77%.", rendered)
        self.assertIn("Pressure decline was 0.043 mbar/min.", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
