"""Card distillation of one paper (memorive/documents/workflow/segmented_distill.py, node 03), end to end.

CoreSegmentedDistiller.distill runs the card distiller once when the paper fits, and splits the paper
into segments when the output limit cuts that pass off or the source is too long; a run that stops
resumes from the segments already done. The card distiller is a stand-in that writes a synthetic card
from the chunks it is given, or answers with an output truncation; nothing leaves the computer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memorive.documents.card_distillation.card import CONTENT_FIELDS, LIST_FIELDS, build_card, write_card
from memorive.documents.card_distillation.config import CARD_SCHEMA_VERSION
from memorive.documents.card_distillation.distill import DistillResult
from memorive.documents.workflow import segmented_distill
from memorive.documents.workflow.segmented_distill import CoreSegmentedDistiller
from memorive.platform.naming import display_title, file_name

PAPER, TITLE, MODEL = "DOC_TEST0001", "Rates of incubation in soil", "gpt-5.6-sol"
CLI_CONFIG = {"nodes": {"03_CARD_DISTILL": {
    "profile_kind": "CLI", "profile_ref": "cli-1",
    "execution_profile": {"service": {"adapter_id": "codex_cli"}, "model": {"model_name": MODEL}}}}}
SHORT = [f"Sentence {i} about incubation rates in soil samples." * 3 for i in range(6)]
LONGER = [f"Paragraph {i}. " + "Incubation rates rose with temperature in every soil sample. " * 12
          for i in range(14)]


def make_paper(folder: Path, texts: list[str]) -> Path:
    folder.mkdir(parents=True)
    name = display_title(TITLE)
    (folder / file_name("CleanMD", PAPER, name, "md")).write_text(
        f"---\npaper_id: {PAPER}\ntitle: {TITLE}\ndata_ownership: self\n---\n" + "\n\n".join(texts),
        encoding="utf-8")
    rows = [{"paper_id": PAPER, "chunk_id": f"{PAPER}#c{i:04d}", "text": text, "data_ownership": "self",
             "section_path": "Results", "page_start": 1, "page_end": 1} for i, text in enumerate(texts, 1)]
    (folder / file_name("Chunks", PAPER, name, "jsonl")).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return folder


class CardDistiller:
    """Stand-in for the card distiller: one synthetic card per call, anchored to the folder's chunks."""

    def __init__(self, truncate=lambda folder, chunk_ids: False, stop_at_call=None):
        self.calls: list[list[str]] = []
        self.truncate, self.stop_at_call = truncate, stop_at_call

    def __call__(self, folder, *, target, overwrite, max_tokens=None, segment_handoff_on_length=False,
                 route_large_input=False):
        folder = Path(folder)
        chunks = next(folder.glob("[[]Chunks[]]*.jsonl")).read_text(encoding="utf-8").splitlines()
        ids = [json.loads(line)["chunk_id"] for line in chunks if line]
        self.calls.append(ids)
        if len(self.calls) == self.stop_at_call:
            raise RuntimeError("the app was closed")
        if self.truncate(folder, ids):
            return DistillResult(status="failed", paper_id=PAPER, error_type="CardParseError",
                                 error_message="model stopped: finish_reason=length")
        card = build_card(
            paper_id=PAPER, title=TITLE, data_ownership="self",
            fields={f: ([f"{f} from {ids[0]}"] if f in LIST_FIELDS else f"{f} from {ids[0]}") for f in CONTENT_FIELDS},
            by_field={f: [{"chunk_id": ids[0], "quote": None, "section_page": None}] for f in CONTENT_FIELDS},
            distill_model=MODEL, distilled_at="2026-11-17 10:00", schema_version=CARD_SCHEMA_VERSION)
        path = folder / file_name("Card", PAPER, display_title(TITLE), "md")
        write_card(path, card, overwrite=overwrite)
        return DistillResult(status="completed", paper_id=PAPER, card_path=str(path), distill_model=MODEL,
                             distilled_at="2026-11-17 10:00", chunk_count=len(ids), self_check={"flags": []})


def distill(tmp_path, paper, card_distiller, **kwargs):
    distiller = CoreSegmentedDistiller(workflow_config=CLI_CONFIG, evidence_root=tmp_path / "evidence")
    return distiller, distiller.distill(paper, legacy_distill=card_distiller, **kwargs)


def all_chunks(texts):
    return [f"{PAPER}#c{i:04d}" for i in range(1, len(texts) + 1)]


def test_a_paper_that_fits_is_distilled_in_one_pass_and_reused_afterwards(tmp_path):
    paper, card_distiller = make_paper(tmp_path / "paper", SHORT), CardDistiller()
    distiller, result = distill(tmp_path, paper, card_distiller)
    assert result.status == "completed" and card_distiller.calls == [all_chunks(SHORT)]
    assert distiller.last_metrics["route"] == "LEGACY_SINGLE_PASS"
    assert distiller.last_metrics["recovered_existing_final_card"] is False

    distiller, again = distill(tmp_path, paper, card_distiller)
    assert len(card_distiller.calls) == 1                     # the card written for this input is reused
    assert again.card_path == result.card_path and distiller.last_metrics["recovered_existing_final_card"]


def test_a_pass_cut_off_by_the_output_limit_continues_in_segments(tmp_path):
    paper = make_paper(tmp_path / "paper", LONGER)
    card_distiller = CardDistiller(truncate=lambda folder, ids: len(ids) == len(LONGER))
    distiller, result = distill(tmp_path, paper, card_distiller)
    assert result.status == "completed" and Path(result.card_path).parent == paper
    assert distiller.last_metrics["route"] == "SEGMENTED_AFTER_OUTPUT_TRUNCATION"
    fallback = json.loads((tmp_path / "evidence" / "adaptive_segment_fallback.json").read_text(encoding="utf-8"))
    assert fallback["trigger"] == "OUTPUT_TRUNCATION_RETRY_EXHAUSTED"
    segments = card_distiller.calls[1:]
    assert len(segments) == distiller.last_metrics["segment_count"] > 1
    assert {chunk for segment in segments for chunk in segment} == set(all_chunks(LONGER))


def test_a_long_source_is_split_and_a_stopped_run_resumes_from_the_segments_done(tmp_path, monkeypatch):
    monkeypatch.setattr(segmented_distill, "INPUT_MAX_CHARS", 3000)
    paper, card_distiller = make_paper(tmp_path / "paper", LONGER), CardDistiller(stop_at_call=3)
    with pytest.raises(RuntimeError, match="the app was closed"):
        distill(tmp_path, paper, card_distiller)
    first = list(card_distiller.calls)

    distiller, result = distill(tmp_path, paper, card_distiller)
    assert result.status == "completed"
    assert distiller.last_metrics["route"] == "SEGMENTED_BY_DOCUMENT_SIZE_ROUTE_SIGNAL"
    resumed = card_distiller.calls[len(first):]
    assert resumed[0] == first[-1] and first[:2] == card_distiller.calls[:2]   # done segments are not sent again
    assert len(first) - 1 + len(resumed) == distiller.last_metrics["segment_count"]

    calls = len(card_distiller.calls)
    distill(tmp_path, paper, card_distiller)
    assert len(card_distiller.calls) == calls                 # a finished run is read back without calls


def test_a_segment_cut_off_by_the_output_limit_is_split_further(tmp_path, monkeypatch):
    monkeypatch.setattr(segmented_distill, "INPUT_MAX_CHARS", 3000)
    paper = make_paper(tmp_path / "paper", LONGER)
    card_distiller = CardDistiller(truncate=lambda folder, ids: "subsegments" not in folder.as_posix() and len(ids) >= 3)
    distiller, result = distill(tmp_path, paper, card_distiller)
    assert result.status == "completed"
    assert list((tmp_path / "evidence" / "segments").glob("*/subsegments/adaptive_segment_fallback.json"))
    assert any(len(ids) < 3 for ids in card_distiller.calls)  # the smaller pieces were distilled


@pytest.mark.parametrize("kwargs, message", [
    ({"force_segment": True}, "FORCE_SEGMENT_PRODUCTION_FORBIDDEN"),
    ({"target": "vault"}, "SEGMENT_TARGET_MUST_BE_SANDBOX"),
])
def test_forced_segments_and_the_vault_are_refused(tmp_path, kwargs, message):
    with pytest.raises(RuntimeError, match=message):
        distill(tmp_path, make_paper(tmp_path / "paper", SHORT), CardDistiller(), **kwargs)
