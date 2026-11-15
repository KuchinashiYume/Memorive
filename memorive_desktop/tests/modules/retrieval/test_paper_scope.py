"""Regression tests for production paper-scoped RETRIEVAL retrieval."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from runtime_log import extract_module_logs
from retrieval import build_context_pack, retrieve_candidates


class FakeCollection:
    def __init__(self, records: list[dict]):
        self.records = records
        self.query_wheres: list[dict | None] = []

    @staticmethod
    def _matches(record: dict, where: dict | None) -> bool:
        return where is None or all(record["metadata"].get(k) == v for k, v in where.items())

    def count(self) -> int:
        return len(self.records)

    def get(self, *, where=None, include=None):
        rows = [record for record in self.records if self._matches(record, where)]
        return {"ids": [record["id"] for record in rows]}

    def query(self, *, query_embeddings, n_results, include, where=None):
        self.query_wheres.append(where)
        rows = [record for record in self.records if self._matches(record, where)][:n_results]
        return {
            "ids": [[record["id"] for record in rows]],
            "documents": [[record["document"] for record in rows]],
            "metadatas": [[record["metadata"] for record in rows]],
            "distances": [[record["distance"] for record in rows]],
        }


class PaperScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="m3_paper_scope_")
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.log_dir = self.root / "runtime_log"
        self.library.mkdir()
        self.previous_env = {
            name: os.environ.get(name)
            for name in (
                "MEMORIVE_RUNTIME_LOG_LOG_DIR",
                "MEMORIVE_RETRIEVAL_RERANK_ENABLED",
                "MEMORIVE_RETRIEVAL_TARGET_RECALL",
                "MEMORIVE_RETRIEVAL_PER_CARD_CAP",
                "MEMORIVE_RETRIEVAL_MAX_OVERFETCH",
            )
        }
        os.environ.update({
            "MEMORIVE_RUNTIME_LOG_LOG_DIR": str(self.log_dir),
            "MEMORIVE_RETRIEVAL_RERANK_ENABLED": "false",
            "MEMORIVE_RETRIEVAL_TARGET_RECALL": "10",
            "MEMORIVE_RETRIEVAL_PER_CARD_CAP": "3",
            "MEMORIVE_RETRIEVAL_MAX_OVERFETCH": "20",
        })
        self._write_card("A", ["A#c1", "A#c2", "A#c3", "A#c4", "A#c5"])
        self._write_card("B", ["B#c1", "B#c2"])
        self.collection = FakeCollection([
            self._record("A#c1", "A", 0.01),
            self._record("B#c1", "B", 0.02),
            self._record("A#c2", "A", 0.03),
            self._record("B#c2", "B", 0.04),
            self._record("A#c3", "A", 0.05),
            self._record("A#c4", "A", 0.06),
            self._record("A#c5", "A", 0.07),
        ])

    def tearDown(self) -> None:
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    def _write_card(self, paper_id: str, chunk_ids: list[str]) -> None:
        folder = self.library / f"[{paper_id}] title"
        folder.mkdir()
        chunks = "\n".join(f"        - {chunk_id}" for chunk_id in chunk_ids)
        (folder / f"[Card][{paper_id}] title.md").write_text(
            "---\n"
            "review_status: active\n"
            "data_ownership: self\n"
            "source_anchor:\n"
            f"  paper_id: {paper_id}\n"
            "  by_field:\n"
            "    method:\n"
            "      section_page: p1\n"
            "      chunk_ids:\n"
            f"{chunks}\n"
            "field_credibility:\n"
            "  method:\n"
            "    credibility: verified\n"
            "---\nbody\n",
            encoding="utf-8",
        )

    @staticmethod
    def _record(chunk_id: str, paper_id: str, distance: float) -> dict:
        return {
            "id": chunk_id,
            "document": f"relevant content from {paper_id} {chunk_id}",
            "metadata": {"paper_id": paper_id, "chunk_id": chunk_id},
            "distance": distance,
        }

    def _retrieve(self, paper_id=None):
        return retrieve_candidates(
            "target article question",
            target="sandbox",
            paper_id=paper_id,
            embed_fn=lambda _question: [1.0, 0.0],
            collection=self.collection,
            lib_root=self.library,
        )

    def test_shared_mode_preserves_cross_paper_behavior(self) -> None:
        candidates = self._retrieve()
        self.assertEqual({candidate.paper_id for candidate in candidates.candidates}, {"A", "B"})
        self.assertEqual(sum(candidate.paper_id == "A" for candidate in candidates.candidates), 3)
        self.assertEqual(self.collection.query_wheres[-1], None)
        self.assertEqual(candidates.scope_mode, "shared")
        self.assertIsNone(candidates.scope_paper_id)

    def test_paper_scope_reaches_chroma_where_and_context_pack(self) -> None:
        candidates = self._retrieve("A")
        self.assertEqual({candidate.paper_id for candidate in candidates.candidates}, {"A"})
        self.assertEqual(candidates.raw_recall_count, 5)
        self.assertEqual(candidates.final_active_count, 5)
        self.assertEqual(len(candidates.candidates), 5)
        self.assertEqual(self.collection.query_wheres[-1], {"paper_id": "A"})
        self.assertEqual(candidates.scope_mode, "paper_id")
        self.assertEqual(candidates.scope_paper_id, "A")

        pack = build_context_pack(candidates)
        self.assertEqual({block.source["paper_id"] for block in pack.blocks}, {"A"})
        self.assertEqual(pack.scope_mode, "paper_id")
        self.assertEqual(pack.scope_paper_id, "A")

        records = [
            json.loads(line)
            for line in extract_module_logs("RETRIEVAL", log_path=self.log_dir / "log.txt")
        ]
        event = [record for record in records if record.get("event") == "CandidateRetrieval 候选召回"][-1]
        self.assertEqual(event["context"]["scope"], {
            "mode": "paper_id", "paper_id": "A", "matched_vector_count": 5,
        })

    def test_unknown_scope_is_empty_without_querying_other_papers(self) -> None:
        before = len(self.collection.query_wheres)
        candidates = self._retrieve("missing")
        self.assertEqual(candidates.candidates, ())
        self.assertEqual(candidates.raw_recall_count, 0)
        self.assertEqual(len(self.collection.query_wheres), before)
        self.assertEqual(candidates.scope_paper_id, "missing")

    def test_blank_scope_fails_loud(self) -> None:
        with self.assertRaisesRegex(ValueError, "paper_id scope"):
            self._retrieve("   ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
