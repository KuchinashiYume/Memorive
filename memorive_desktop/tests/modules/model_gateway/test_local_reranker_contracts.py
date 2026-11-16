from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from memorive.models.gateway.local_reranker.contracts import LocalRerankerViolation, build_request, render_document_for_reranker, validate_compatibility_manifest
from memorive.models.gateway.local_reranker.contracts import file_sha256
from tests.modules.model_gateway.local_reranker_fixture import _write, make_fake_manifest, provenance


class LocalRerankerContractTests(unittest.TestCase):
    def test_request_is_gold_free_and_index_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = make_fake_manifest(Path(temp))
            request = build_request(
                [{"query_id": "Q1", "query": "answer?", "documents": [{"candidate_id": "C1", "text": "answer", "provenance": provenance()}]}],
                manifest,
            )
            self.assertFalse(request["gold_or_scorer_present"])
            self.assertEqual(request["batches"][0]["documents"][0]["index"], 0)
            document = request["batches"][0]["documents"][0]
            self.assertIn("source_role: results", document["model_input"])
            self.assertIn("page_family: digital", document["model_input"])
            self.assertEqual(
                document["model_input"],
                render_document_for_reranker("answer", provenance()),
            )
            self.assertRegex(document["model_input_sha256"], r"^[A-F0-9]{64}$")
            contaminated = [{"query_id": "Q1", "query": "answer?", "labels": {}, "documents": [{"candidate_id": "C1", "text": "answer", "provenance": provenance()}]}]
            with self.assertRaisesRegex(LocalRerankerViolation, "SUBJECT_GOLD_FIELD_FORBIDDEN"):
                build_request(contaminated, manifest)

    def test_request_without_provenance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = make_fake_manifest(Path(temp))
            with self.assertRaisesRegex(LocalRerankerViolation, "DOCUMENT_PROVENANCE_MISSING"):
                build_request(
                    [{"query_id": "Q1", "query": "answer?", "documents": [{"candidate_id": "C1", "text": "answer"}]}],
                    manifest,
                )

    def test_cloud_or_partial_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manifest = make_fake_manifest(Path(temp))
            changed = deepcopy(manifest)
            changed["cloud_fallback"] = True
            with self.assertRaisesRegex(LocalRerankerViolation, "CLOUD_FALLBACK_FORBIDDEN"):
                validate_compatibility_manifest(changed)
            changed = deepcopy(manifest)
            changed["score_contract"]["partial_batch_acceptance"] = True
            with self.assertRaisesRegex(LocalRerankerViolation, "SCORE_CONTRACT_INVALID"):
                validate_compatibility_manifest(changed)


if __name__ == "__main__":
    unittest.main()
