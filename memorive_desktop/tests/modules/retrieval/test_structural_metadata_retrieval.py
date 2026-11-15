"""quality-A tests for vector metadata and legacy Chunks sidecar fallback."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from retrieval.candidate_retrieval import _query, resolve_chunk_metadata


class FakeCollection:
    def query(self, **_kwargs):
        return {
            "ids": [["Paper#c1"]],
            "documents": [["table body"]],
            "metadatas": [[{
                "paper_id": "Paper",
                "chunk_id": "Paper#c1",
                "section_path": "Results",
                "block_type": "table",
                "page_start": 3,
                "page_end": 4,
            }]],
            "distances": [[0.1]],
        }


class StructuralMetadataRetrievalTest(unittest.TestCase):
    def test_query_preserves_available_vector_structure_metadata(self) -> None:
        hit = _query(FakeCollection(), [1.0, 0.0], 1)[0]
        self.assertEqual(hit["section_path"], "Results")
        self.assertEqual(hit["block_type"], "table")
        self.assertEqual(hit["page_start"], 3)
        self.assertEqual(hit["page_end"], 4)
        self.assertEqual(hit["metadata_status"], "vector")

    def test_legacy_vector_can_recover_structure_from_same_paper_chunks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m3_metadata_fallback_") as tmp:
            library = Path(tmp)
            folder = library / "[Paper] title"
            folder.mkdir()
            chunks_path = folder / "[Chunks][Paper] title.jsonl"
            record = {
                "paper_id": "Paper",
                "chunk_id": "Paper#c1",
                "text": "table body",
                "section_path": "Results",
                "block_type": "table",
                "page_start": 3,
                "page_end": 4,
            }
            chunks_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            metadata = resolve_chunk_metadata("Paper", "Paper#c1", library, {})

        self.assertEqual(metadata["section_path"], "Results")
        self.assertEqual(metadata["block_type"], "table")
        self.assertEqual(metadata["page_start"], 3)
        self.assertEqual(metadata["page_end"], 4)
        self.assertEqual(metadata["metadata_status"], "chunk_sidecar")

    def test_missing_legacy_sidecar_returns_explicit_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m3_metadata_unknown_") as tmp:
            metadata = resolve_chunk_metadata("Paper", "Paper#c1", Path(tmp), {})
        self.assertEqual(metadata, {"metadata_status": "unknown"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
