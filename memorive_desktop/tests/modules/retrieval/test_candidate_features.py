from __future__ import annotations

import unittest

from memorive.research.retrieval.retrieval_calibration.candidate_features import build_reranker_provenance, resolve_feature_envelope
BASE = {"candidate_id": "A", "paper_id": "P", "chunk_id": "C", "text": "text", "distance": 0.1}


class CandidateFeatureTests(unittest.TestCase):
    def test_vector_preferred_and_sidecar_fills_missing(self) -> None:
        result = resolve_feature_envelope(
            BASE,
            vector_metadata={"source_role": "results", "page_start": 2},
            sidecar_metadata={"source_role": "results", "block_type": "body"},
        )
        self.assertEqual(result["source_role"], "results")
        self.assertEqual(result["block_type"], "body")
        self.assertEqual(result["metadata_status"], "mixed")

    def test_conflict_is_explicit_and_does_not_guess(self) -> None:
        result = resolve_feature_envelope(
            BASE,
            vector_metadata={"source_role": "results"},
            sidecar_metadata={"source_role": "references"},
        )
        self.assertEqual(result["metadata_status"], "inconsistent")
        self.assertIsNone(result["source_role"])
        self.assertEqual(result["conflicting_fields"], ["source_role"])

    def test_source_identity_stable_across_metadata(self) -> None:
        first = resolve_feature_envelope(BASE, vector_metadata={"source_role": "body"})
        second = resolve_feature_envelope(BASE, sidecar_metadata={"source_role": "body"})
        self.assertEqual(first["source_identity_sha256"], second["source_identity_sha256"])

    def test_page_family_alias_and_source_role_are_bound_for_reranker(self) -> None:
        envelope = resolve_feature_envelope(
            BASE,
            vector_metadata={
                "page_family_primary": "table_numeric",
                "modifiers": ["multicolumn", "blur"],
                "page_start": 4,
                "page_end": 4,
                "source_role": "results",
            },
        )
        provenance = build_reranker_provenance(
            {**BASE, **envelope},
            retrieval_terminal_sha256="A" * 64,
        )
        self.assertEqual(provenance["page_family"], "table_numeric")
        self.assertEqual(provenance["page_family_modifiers"], ["blur", "multicolumn"])
        self.assertEqual(provenance["source_role"], "results")
        self.assertEqual(provenance["page_span"], {"start": 4, "end": 4})
        self.assertEqual(provenance["retrieval_terminal_sha256"], "A" * 64)
        self.assertRegex(provenance["provenance_sha256"], r"^[A-F0-9]{64}$")

    def test_missing_page_family_and_role_remain_explicit_unknown(self) -> None:
        provenance = build_reranker_provenance(BASE, retrieval_terminal_sha256="B" * 64)
        self.assertEqual(provenance["page_family"], "unknown")
        self.assertEqual(provenance["source_role"], "unknown")
        self.assertEqual(provenance["page_span"], {"start": None, "end": None})



if __name__ == "__main__":
    unittest.main()
