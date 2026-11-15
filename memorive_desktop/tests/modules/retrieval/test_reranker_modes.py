"""LOCAL-RETRIEVAL proves reranker OFF parity and deterministic injection only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from runtime_log import extract_module_logs
from retrieval import build_context_pack, retrieve_candidates  # noqa: E402


class FakeCollection:
    def __init__(self, records: list[dict]):
        self.records = records

    def count(self) -> int:
        return len(self.records)

    def query(self, *, query_embeddings, n_results, include, where=None):
        rows = self.records[:n_results]
        return {
            "ids": [[row["id"] for row in rows]],
            "documents": [[row["document"] for row in rows]],
            "metadatas": [[row["metadata"] for row in rows]],
            "distances": [[row["distance"] for row in rows]],
        }


class RerankerModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="reranker_modes_")
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.log_dir = self.root / "runtime_log"
        self.library.mkdir()
        self.card = self.library / "[P11] synthetic"
        self.card.mkdir()
        self.card_path = self.card / "[Card][P11] synthetic.md"
        self._write_card("self")
        self.collection = FakeCollection([
            self._record("P11#c1", 0.05),
            self._record("P11#c2", 0.10),
            self._record("P11#c3", 0.20),
        ])
        self.previous = {
            name: os.environ.get(name)
            for name in (
                "MEMORIVE_RUNTIME_LOG_LOG_DIR",
                "MEMORIVE_RETRIEVAL_RERANK_ENABLED",
                "MEMORIVE_RETRIEVAL_RERANK_THRESHOLD",
                "MEMORIVE_RETRIEVAL_RANK_MODE",
                "MEMORIVE_RETRIEVAL_TARGET_RECALL",
                "MEMORIVE_RETRIEVAL_PER_CARD_CAP",
                "MEMORIVE_RETRIEVAL_MAX_OVERFETCH",
            )
        }
        os.environ.update({
            "MEMORIVE_RUNTIME_LOG_LOG_DIR": str(self.log_dir),
            "MEMORIVE_RETRIEVAL_RERANK_ENABLED": "false",
            "MEMORIVE_RETRIEVAL_RERANK_THRESHOLD": "0",
            "MEMORIVE_RETRIEVAL_TARGET_RECALL": "3",
            "MEMORIVE_RETRIEVAL_PER_CARD_CAP": "3",
            "MEMORIVE_RETRIEVAL_MAX_OVERFETCH": "3",
        })
        os.environ.pop("MEMORIVE_RETRIEVAL_RANK_MODE", None)

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temporary.cleanup()

    def _write_card(self, ownership: str) -> None:
        self.card_path.write_text(
            "---\n"
            "review_status: active\n"
            f"data_ownership: {ownership}\n"
            "source_anchor:\n"
            "  paper_id: P11\n"
            "  by_field:\n"
            "    method:\n"
            "      section_page: p1\n"
            "      chunk_ids:\n"
            "        - P11#c1\n"
            "        - P11#c2\n"
            "        - P11#c3\n"
            "field_credibility:\n"
            "  method:\n"
            "    credibility: verified\n"
            "---\nsynthetic body\n",
            encoding="utf-8",
        )

    @staticmethod
    def _record(chunk_id: str, distance: float) -> dict:
        return {
            "id": chunk_id,
            "document": f"public-safe synthetic text {chunk_id}",
            "metadata": {
                "paper_id": "P11",
                "chunk_id": chunk_id,
                "page_family": "multicolumn",
                "source_role": "methods" if chunk_id.endswith("2") else "body",
            },
            "distance": distance,
        }

    def _retrieve(self, rerank_fn):
        return retrieve_candidates(
            "public-safe synthetic question",
            target="sandbox",
            embed_fn=lambda _question: [1.0, 0.0],
            rerank_fn=rerank_fn,
            collection=self.collection,
            lib_root=self.library,
        )

    def _last_rerank_event(self) -> dict:
        rows = [
            json.loads(line)
            for line in extract_module_logs("RETRIEVAL", log_path=self.log_dir / "log.txt")
        ]
        return [row for row in rows if row.get("event") == "CandidateRetrieval 候选召回"][-1]

    def test_off_mode_preserves_distance_order_without_calling_reranker(self) -> None:
        calls = 0

        def forbidden(*_args):
            nonlocal calls
            calls += 1
            raise AssertionError("reranker must remain off")

        result = self._retrieve(forbidden)
        self.assertEqual(calls, 0)
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c1", "P11#c2", "P11#c3"],
        )
        self.assertTrue(all(candidate.rerank_score is None for candidate in result.candidates))
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertFalse(state["enabled"])
        self.assertFalse(state["attempted"])

    def test_deterministic_injection_reorders_without_real_model(self) -> None:
        os.environ["MEMORIVE_RETRIEVAL_RERANK_ENABLED"] = "true"
        calls: list[tuple[str, tuple[str, ...]]] = []

        def deterministic(question: str, texts: list[str]):
            calls.append((question, tuple(texts)))
            return [0.1, 0.9, 0.5]

        result = self._retrieve(deterministic)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c2", "P11#c3", "P11#c1"],
        )
        self.assertEqual(
            [candidate.rerank_score for candidate in result.candidates],
            [0.9, 0.5, 0.1],
        )
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertTrue(state["enabled"])
        self.assertTrue(state["attempted"])
        self.assertFalse(state["skipped"])
        self.assertIsNone(state["fallback_reason"])

    def test_invalid_injected_scores_fall_back_to_distance(self) -> None:
        os.environ["MEMORIVE_RETRIEVAL_RERANK_ENABLED"] = "true"
        result = self._retrieve(lambda _question, _texts: [0.5])
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c1", "P11#c2", "P11#c3"],
        )
        self.assertTrue(all(candidate.rerank_score is None for candidate in result.candidates))
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertEqual(state["fallback_reason"], "invalid_scores")

    def test_entrusted_content_skips_injected_or_cloud_reranker(self) -> None:
        os.environ["MEMORIVE_RETRIEVAL_RERANK_ENABLED"] = "true"
        self._write_card("entrusted")
        calls = 0

        def forbidden(*_args):
            nonlocal calls
            calls += 1
            raise AssertionError("entrusted text must not leave the local boundary")

        result = self._retrieve(forbidden)
        self.assertEqual(calls, 0)
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c1", "P11#c2", "P11#c3"],
        )
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertTrue(state["skipped"])
        self.assertEqual(state["skip_reason"], "entrusted_present")

    def test_local_multimodal_listwise_reorders_entrusted_and_reaches_context_pack(self) -> None:
        os.environ["MEMORIVE_RETRIEVAL_RANK_MODE"] = "multimodal_listwise"
        self._write_card("entrusted")
        calls = []

        def local_rank(question: str, documents: list[dict], query_intent: str):
            calls.append((question, documents, query_intent))
            by_id = {row["candidate_id"]: row for row in documents}
            self.assertEqual(by_id["P11#c2"]["provenance"]["page_family"], "multicolumn")
            self.assertEqual(by_id["P11#c2"]["provenance"]["source_role"], "methods")
            return {
                "candidate_ids": ["P11#c2", "P11#c3", "P11#c1"],
                "receipt": {
                    "schema_version": "RetrievalCalibrationLocalMultimodalListwiseRankReceipt-v1",
                    "status": "PASS",
                    "receipt_sha256": "A" * 64,
                    "requested_model": "qwen3.8:27b-q4_K_M",
                    "returned_model": "qwen3.8:27b-q4_K_M",
                    "model_digest": "B" * 64,
                    "latency_ms": 10,
                    "local_metadata_request_count": 1,
                    "local_model_request_count": 1,
                    "external_request_count": 0,
                    "cloud_fallback_count": 0,
                    "dedicated_reranker_fallback_count": 0,
                    "production_mutation_count": 0,
                },
            }

        result = retrieve_candidates(
            "public-safe synthetic method question",
            target="sandbox",
            embed_fn=lambda _question: [1.0, 0.0],
            multimodal_rank_fn=local_rank,
            collection=self.collection,
            lib_root=self.library,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c2", "P11#c3", "P11#c1"],
        )
        self.assertEqual([candidate.ranking_rank for candidate in result.candidates], [1, 2, 3])
        self.assertTrue(all(candidate.rerank_score is None for candidate in result.candidates))
        pack = build_context_pack(result)
        self.assertEqual(
            [block.source["chunk_id"] for block in pack.blocks],
            ["P11#c2", "P11#c3", "P11#c1"],
        )
        self.assertEqual(pack.blocks[0].source["ranking_mode"], "GENERATIVE_LISTWISE_EXACT_SET")
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertEqual(state["mode"], "multimodal_listwise")
        self.assertFalse(state["dedicated_reranker_required"])
        self.assertEqual(state["execution_receipt"]["external_request_count"], 0)

    def test_invalid_listwise_exact_set_falls_back_atomically(self) -> None:
        os.environ["MEMORIVE_RETRIEVAL_RANK_MODE"] = "multimodal_listwise"
        result = retrieve_candidates(
            "public-safe synthetic question",
            target="sandbox",
            embed_fn=lambda _question: [1.0, 0.0],
            multimodal_rank_fn=lambda *_args: ["P11#c1", "P11#c1", "P11#c2"],
            collection=self.collection,
            lib_root=self.library,
        )
        self.assertEqual(
            [candidate.chunk_id for candidate in result.candidates],
            ["P11#c1", "P11#c2", "P11#c3"],
        )
        self.assertTrue(all(candidate.ranking_rank is None for candidate in result.candidates))
        state = self._last_rerank_event()["context"]["rerank"]
        self.assertEqual(state["fallback_reason"], "invalid_candidate_exact_set")


if __name__ == "__main__":
    unittest.main(verbosity=2)
