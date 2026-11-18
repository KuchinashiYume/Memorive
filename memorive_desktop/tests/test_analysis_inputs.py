"""What the analysis refuses before calling the model, and how it sorts the model's claims
(research/analysis/analysis.py), on a pack of made-up chunks and a stand-in model.
"""
from __future__ import annotations

import json

import pytest

from memorive.research.analysis.analysis import AnalysisError, OwnershipFailClosed, analyze
from memorive.research.retrieval.context_pack import build_context_pack
from memorive.research.retrieval.ownership import bind_authority_projection, snapshot_fields
from memorive.research.retrieval.types import Candidate, CandidateSet

QUESTION = "How did growth change at 37 C?"


def candidate(index, text, chunk_id=None):
    cid = chunk_id or f"Paper#c{index}"
    bound = bind_authority_projection(subject_ref=f"chunk:Paper:{cid}", authoritative_value="self", assertion_ref=f"OS-{index}",
                                      revision=1, basis_ref=f"basis:{index}", projection_value="self",
                                      projection_revision=1, projection_ref=f"vector:{cid}:{index}")
    authority = bound.ownership_contributors[0]
    return Candidate(chunk_id=cid, paper_id="Paper", text=text, distance=index / 100, section_path="Results",
                     block_type="paragraph", metadata_status="vector", source_role="direct_result",
                     ownership_assertion_ref=authority.assertion_ref, ownership_revision=authority.revision,
                     ownership_basis_ref=authority.basis_ref, ownership_projection_value="self",
                     ownership_projection_revision=1, **snapshot_fields(bound))


def pack(*items):
    return build_context_pack(CandidateSet(candidates=tuple(items), target_active_count=len(items),
                                           raw_recall_count=len(items), filtered_non_active_count=0,
                                           final_active_count=len(items), reached_overfetch_cap=False))


GROWTH = candidate(1, "Growth at 37 C was twice the growth at 25 C.")
WEATHER = candidate(2, "The laboratory was repainted in spring.")


class Model:
    def __init__(self, **sections):
        self.calls = []
        self.sections = {"literature_support": [], "possibly_irrelevant": [], "ai_general_knowledge": [],
                         "risks_and_uncertainties": [], "recheck_locations": [], **sections}

    def __call__(self, payload):
        self.calls.append(payload)
        return {"text": json.dumps(self.sections), "model": "stand-in"}


def run(context, model, question=QUESTION, **kwargs):
    return analyze(context, question, model_call=model, region_check=lambda: None, **kwargs)


def test_an_empty_question_is_refused_before_the_model_is_called():
    model = Model()
    with pytest.raises(ValueError, match="RESEARCH_ANALYSIS"):
        run(pack(GROWTH), model, question="  ")
    assert model.calls == []


def test_the_old_and_new_ownership_arguments_must_agree():
    model = Model()
    with pytest.raises(OwnershipFailClosed):
        run(pack(GROWTH), model, data_ownership="self", legacy_data_ownership="entrusted")
    assert model.calls == []


def test_a_repeated_chunk_is_refused_before_the_model_is_called():
    model = Model()
    with pytest.raises((AnalysisError, OwnershipFailClosed)):
        run(pack(GROWTH, candidate(3, "Growth again.", chunk_id=GROWTH.chunk_id)), model)
    assert model.calls == []


def test_the_model_s_claims_are_sorted_by_the_chunks_they_cite():
    model = Model(
        literature_support=[
            {"text": "Growth doubled at 37 C.", "source_chunk_id": GROWTH.chunk_id},
            {"text": "Repainting mattered.", "source_chunk_id": WEATHER.chunk_id},
            {"text": "An invented source.", "source_chunk_id": "Paper#c99"},
        ],
        possibly_irrelevant=[{"text": "Repainting is off topic.", "source_chunk_id": WEATHER.chunk_id, "reason": "not about growth"}])
    result = run(pack(GROWTH, WEATHER), model)
    assert [(c.text, c.source_id["chunk_id"]) for c in result.literature_support] == [("Growth doubled at 37 C.", GROWTH.chunk_id)]
    assert [c.source_id["chunk_id"] for c in result.possibly_irrelevant] == [WEATHER.chunk_id]   # off topic wins
    assert [c.claimed_chunk_id for c in result.flagged_unsourced] == ["Paper#c99"]
    assert result.analysis_ref.startswith("A-") and result.ownership_binding_status == "matched"
