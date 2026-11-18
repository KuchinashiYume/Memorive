"""Candidate retrieval paths the other retrieval tests do not reach (research/retrieval/candidate_retrieval.py),
with a stand-in vector collection and made-up cards: cards that are not active, the verified-only
mode, the reranker's threshold, the automatic route with one candidate, and a local ranker whose
receipt shows an outside request.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memorive.research.retrieval import retrieve_candidates


class Collection:
    def __init__(self, records):
        self.records = records

    def count(self):
        return len(self.records)

    def get(self, *, where=None, include=None):
        return {"ids": [r["id"] for r in self.records]}

    def query(self, *, query_embeddings, n_results, include, where=None):
        rows = self.records[:n_results]
        return {"ids": [[r["id"] for r in rows]], "documents": [[r["document"] for r in rows]],
                "metadatas": [[r["metadata"] for r in rows]], "distances": [[r["distance"] for r in rows]]}


def record(chunk_id, paper_id, distance):
    return {"id": chunk_id, "document": f"growth result {chunk_id}", "distance": distance,
            "metadata": {"paper_id": paper_id, "chunk_id": chunk_id}}


def card(library: Path, paper_id, chunk_ids, status="active"):
    folder = library / f"[{paper_id}] title"
    folder.mkdir()
    chunks = "\n".join(f"        - {c}" for c in chunk_ids)
    (folder / f"[Card][{paper_id}] title.md").write_text(
        f"---\nreview_status: {status}\ndata_ownership: self\nsource_anchor:\n  paper_id: {paper_id}\n  by_field:\n"
        f"    method:\n      section_page: p1\n      chunk_ids:\n{chunks}\n---\nbody\n", encoding="utf-8")


@pytest.fixture
def library(tmp_path, monkeypatch):
    for name, value in {"MEMORIVE_RUNTIME_LOG_LOG_DIR": str(tmp_path / "log"), "MEMORIVE_RETRIEVAL_TARGET_RECALL": "10",
                        "MEMORIVE_RETRIEVAL_PER_CARD_CAP": "3", "MEMORIVE_RETRIEVAL_MAX_OVERFETCH": "20"}.items():
        monkeypatch.setenv(name, value)
    for name in ("MEMORIVE_RETRIEVAL_RERANK_ENABLED", "MEMORIVE_RETRIEVAL_RANK_MODE", "MEMORIVE_RETRIEVAL_RERANK_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "library"
    root.mkdir()
    card(root, "A", ["A#c1", "A#c2", "A#c3"])
    card(root, "P", ["P#c1"], status="pending")
    card(root, "Q", ["Q#c1"], status="quarantined")
    return root


def retrieve(library, records, **kwargs):
    return retrieve_candidates("How did growth change?", embed_fn=lambda q: [1.0, 0.0], collection=Collection(records),
                               lib_root=library, **kwargs)


def ids(result):
    return [c.chunk_id for c in result.candidates]


def test_chunks_of_cards_that_are_pending_quarantined_or_missing_are_left_out(library):
    result = retrieve(library, [record("A#c1", "A", .1), record("P#c1", "P", .2), record("Q#c1", "Q", .3),
                                record("M#c1", "M", .4)])
    assert ids(result) == ["A#c1"] and result.filtered_non_active_count == 3


def test_the_verified_only_mode_keeps_nothing_a_card_review_has_not_admitted(library):
    result = retrieve(library, [record("A#c1", "A", .1)], require_verified=True)
    assert ids(result) == [] and result.filtered_non_active_count == 1 and result.require_verified


def test_the_reranker_s_threshold_cuts_low_scores_and_orders_the_rest(library, monkeypatch):
    monkeypatch.setenv("MEMORIVE_RETRIEVAL_RANK_MODE", "legacy_score")
    monkeypatch.setenv("MEMORIVE_RETRIEVAL_RERANK_THRESHOLD", "0.5")
    result = retrieve(library, [record("A#c1", "A", .1), record("A#c2", "A", .2), record("A#c3", "A", .3)],
                      rerank_fn=lambda question, texts: [0.9, 0.1, 0.6])
    assert ids(result) == ["A#c1", "A#c3"] and [c.rerank_score for c in result.candidates] == [0.9, 0.6]


def test_the_automatic_route_does_not_rank_a_single_candidate(library, monkeypatch):
    monkeypatch.setenv("MEMORIVE_RETRIEVAL_RANK_MODE", "auto")
    calls = []
    result = retrieve(library, [record("A#c1", "A", .1)], multimodal_rank_fn=lambda *a: calls.append(a))
    assert ids(result) == ["A#c1"] and calls == []


def test_a_local_ranker_whose_receipt_shows_an_outside_request_is_not_used(library, monkeypatch):
    monkeypatch.setenv("MEMORIVE_RETRIEVAL_RANK_MODE", "multimodal_listwise")

    def ranker(question, documents, intent):
        receipt = {"status": "PASS", "external_request_count": 1, "cloud_fallback_count": 0,
                   "dedicated_reranker_fallback_count": 0, "requested_model": "local", "returned_model": "local"}
        return {"candidate_ids": ["A#c3", "A#c2", "A#c1"], "receipt": receipt}
    result = retrieve(library, [record("A#c1", "A", .1), record("A#c2", "A", .2), record("A#c3", "A", .3)],
                      multimodal_rank_fn=ranker)
    assert ids(result) == ["A#c1", "A#c2", "A#c3"]                     # the distance order
    assert all(c.ranking_rank is None for c in result.candidates)
