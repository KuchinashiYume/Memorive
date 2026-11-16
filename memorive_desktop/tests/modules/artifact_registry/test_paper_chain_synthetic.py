"""Registering one paper's six-file chain (PDF, RawMD, CleanMD, Chunks, Card, Analysis), with a synthetic paper.

The tests that use a real paper from the private library stay outside the repository
(test_01_p1_replay.py and the files next to it). These tests write a small made-up paper in the same
layout and check the same things: what the adapter links, what it leaves unresolved, that
registering twice changes nothing, and that the source files are not modified.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from memorive.documents.registry import ArtifactRegistry, lineage_status, register_adaptation, sha256_file
from memorive.documents.registry.adapters import P1LegacyAdapter

PAPER = "SYNTHETIC-PAPER-1"
CHUNKS = {
    "c1": "Soil moisture rose by 12 percent after the cover crop was planted.",
    "c2": "The effect was not seen in the sandy plots.",
}
CLOCK_AT = "2026-07-17T08:00:00+08:00"


def _front(fields: dict, body: str) -> str:
    return "---\n" + json.dumps(fields, ensure_ascii=False, indent=1) + "\n---\n" + body + "\n"


def write_paper(folder: Path, *, card_chunk: str = "c1", card_quote: str = "rose by 12 percent",
                missing_anchor: bool = False) -> None:
    """The six files of one paper, named the way the library names them: [Role]title.ext.

    card_chunk and card_quote are the card's first anchor; missing_anchor adds one that points
    at a chunk the Chunks file does not have."""
    folder.mkdir(parents=True)
    (folder / "[PDF]synthetic paper.pdf").write_bytes(b"%PDF-1.4\n% synthetic test file\n")
    (folder / "[RawMD]synthetic paper.md").write_text(_front(
        {"paper_id": PAPER, "convert_engine": "synthetic", "original_format": "pdf",
         "converted_at": "2026-07-17T07:00:00+08:00"}, "Raw text."), encoding="utf-8")
    (folder / "[CleanMD]synthetic paper.md").write_text(_front(
        {"paper_id": PAPER, "source_rawmd": "[RawMD]synthetic paper.md",
         "cleaned_at": "2026-07-17T07:10:00+08:00"}, "Clean text."), encoding="utf-8")
    rows = [{"paper_id": PAPER, "chunk_id": chunk_id, "source_file": "[CleanMD]synthetic paper.md",
             "chunk_hash": chunk_id.upper() * 32, "text": text} for chunk_id, text in CHUNKS.items()]
    (folder / "[Chunks]synthetic paper.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    limitations = [{"chunk_id": "c2", "quote": "not seen in the sandy plots"}]
    if missing_anchor:
        limitations.append({"chunk_id": "c9", "quote": "a chunk that is not there"})
    (folder / "[Card]synthetic paper.md").write_text(_front(
        {"schema_version": 6, "review_status": "active",
         "source_anchor": {"paper_id": PAPER, "by_field": {
             "key_findings": [{"chunk_id": card_chunk, "quote": card_quote}],
             "limitations": limitations}}},
        "Card text."), encoding="utf-8")
    (folder / "[Analysis]synthetic paper.md").write_text(_front(
        {"paper_id": PAPER, "artifact_type": "analysis", "analysis_model": "synthetic",
         "prompt_version": "v1", "test_run_id": "synthetic-run-1", "analyzed_at": "2026-07-17T07:30:00+08:00",
         "context_pack": {"chunk_ids": ["c1", "c2"], "paper_ids": [PAPER]}}, "Analysis text."), encoding="utf-8")


class SyntheticPaperChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self._temporary.name)
        self.registry = ArtifactRegistry(self.temp / "registry", corruption_evidence_root=self.temp / "corruption",
                                         clock=lambda: CLOCK_AT)
        self.adapter = P1LegacyAdapter(authority_run_ref="synthetic-run-1", registered_at=CLOCK_AT,
                                       release_manifest_ref="synthetic://release-manifest")
        self.paper = self.temp / "paper"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_six_files_register_once_without_changing_the_sources(self) -> None:
        write_paper(self.paper)
        before = {path.name: sha256_file(path) for path in self.paper.iterdir()}
        result = self.adapter.adapt_bonakdarpour(self.paper)
        self.assertEqual(register_adaptation(self.registry, result), ["appended"] * 6)
        self.assertEqual(register_adaptation(self.registry, result), ["noop"] * 6)
        self.assertEqual({path.name: sha256_file(path) for path in self.paper.iterdir()}, before)
        self.assertEqual(len(self.registry.read_events()), 6)
        self.assertEqual(self.registry.audit_integrity(), [])

    def test_each_step_links_to_the_file_it_came_from(self) -> None:
        write_paper(self.paper)
        result = self.adapter.adapt_bonakdarpour(self.paper)
        raw, clean, chunks = (result.by_type(t) for t in ("raw_markdown", "clean_markdown", "chunks_jsonl"))
        card, analysis = result.by_type("card_markdown"), result.by_type("analysis_markdown")
        # The RawMD names no exact PDF, so that link stays open instead of being guessed from the folder.
        self.assertEqual(lineage_status(raw), "unknown")
        self.assertEqual([x["requirement"] for x in raw["unresolved_parent_requirements"]], ["exact_pdf_parent"])
        self.assertEqual(clean["parent_artifacts"][0]["parent_artifact_id"], raw["artifact_id"])
        self.assertEqual(clean["parent_artifacts"][0]["relation"], "cleaned_from")
        self.assertEqual(chunks["parent_artifacts"][0]["relation"], "chunked_from")
        self.assertEqual(card["parent_artifacts"][0]["relation"], "distilled_from_chunks")
        self.assertEqual(card["parent_artifacts"][0]["parent_artifact_id"], chunks["artifact_id"])
        self.assertEqual(analysis["parent_artifacts"][0]["parent_artifact_id"], chunks["artifact_id"])
        self.assertEqual(analysis["run_ref"], "synthetic-run-1")
        self.assertEqual({x["requirement"] for x in analysis["unresolved_parent_requirements"]},
                         {"exact_card_version", "independent_context_pack_artifact"})
        self.assertFalse(result.mapping_summary["context_pack_artifact_registered"])

    def test_a_card_anchor_to_a_missing_chunk_is_reported_not_linked(self) -> None:
        write_paper(self.paper, card_chunk="c9")
        card = self.adapter.adapt_bonakdarpour(self.paper).by_type("card_markdown")
        self.assertEqual(card["parent_artifacts"], [])
        self.assertEqual(card["metadata"]["missing_anchor_ids"], ["c9"])
        unresolved = {x["requirement"]: x for x in card["unresolved_parent_requirements"]}
        self.assertEqual(unresolved["source_chunks"]["reason_code"], "CARD_SOURCE_ANCHORS_DO_NOT_FULLY_RESOLVE")
        self.assertEqual(card["conflicting_candidates"], [])

    def test_a_card_quote_that_contradicts_its_chunk_is_a_conflict(self) -> None:
        write_paper(self.paper, card_quote="fell by 40 percent")
        result = self.adapter.adapt_bonakdarpour(self.paper)
        card = result.by_type("card_markdown")
        self.assertEqual(card["parent_artifacts"], [])
        self.assertEqual(lineage_status(card), "conflicted")
        self.assertEqual([x["reason_code"] for x in card["conflicting_candidates"]],
                         ["CARD_QUOTE_CONFLICTS_WITH_CHUNK_TEXT"])
        self.assertNotIn("source_chunks", {x["requirement"] for x in card["unresolved_parent_requirements"]})
        self.assertEqual(card["metadata"]["quote_mismatches"], ["c1"])
        self.assertEqual(register_adaptation(self.registry, result), ["appended"] * 6)

    def test_a_missing_anchor_and_a_contradicting_quote_together_are_a_conflict(self) -> None:
        write_paper(self.paper, card_quote="fell by 40 percent", missing_anchor=True)
        card = self.adapter.adapt_bonakdarpour(self.paper).by_type("card_markdown")
        self.assertEqual(lineage_status(card), "conflicted")
        self.assertEqual([x["requirement"] for x in card["conflicting_candidates"]], ["source_chunks"])
        self.assertNotIn("source_chunks", {x["requirement"] for x in card["unresolved_parent_requirements"]})
        self.assertEqual(card["metadata"]["missing_anchor_ids"], ["c9"])
        self.assertEqual(card["metadata"]["quote_mismatches"], ["c1"])


if __name__ == "__main__":
    unittest.main()
