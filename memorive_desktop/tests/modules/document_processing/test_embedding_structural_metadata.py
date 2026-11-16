"""quality-A tests for additive DocumentChunk structure metadata propagation into vectors."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.processing.document_embed_embed import _vector_metadata


class EmbeddingStructuralMetadataTest(unittest.TestCase):
    def test_m1d_structure_fields_are_preserved_for_new_vectors(self) -> None:
        record = {
            "paper_id": "Paper",
            "chunk_id": "Paper#c0016",
            "page_start": 3,
            "page_end": 4,
            "section_path": "Results",
            "block_type": "table",
            "chunk_schema_version": "2",
        }

        metadata = _vector_metadata(
            record,
            ownership="self",
            provider_model_id="BAAI/bge-m3",
            backend="cloud",
            embedded_at="2026-07-14T23:00+08:00",
            run_id="run-1",
        )

        self.assertEqual(metadata["page_start"], 3)
        self.assertEqual(metadata["page_end"], 4)
        self.assertEqual(metadata["section_path"], "Results")
        self.assertEqual(metadata["block_type"], "table")
        self.assertEqual(metadata["chunk_schema_version"], "2")

    def test_missing_structure_fields_remain_absent_not_invented(self) -> None:
        record = {
            "paper_id": "Paper",
            "chunk_id": "Paper#legacy",
            "chunk_schema_version": "1",
        }

        metadata = _vector_metadata(
            record,
            ownership="self",
            provider_model_id="BAAI/bge-m3",
            backend="cloud",
            embedded_at="2026-07-14T23:00+08:00",
            run_id="run-2",
        )

        for key in ("page_start", "page_end", "section_path", "block_type"):
            self.assertNotIn(key, metadata)


if __name__ == "__main__":
    unittest.main(verbosity=2)
