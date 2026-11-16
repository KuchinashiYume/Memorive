"""Job folders written by earlier versions keep pickles that name classes by module path.

A processed paper's job folder holds 06_context_pack.pkl (a ContextPack) and 07_analysis.pkl (an
AnalysisResult), written with pickle.HIGHEST_PROTOCOL by memorive_workflow/executor.py; an analysis
revision keeps pickle.dumps of an AnalysisResult (evidence_review/analysis_revision.py). The samples
in golden/old_job_pickles were written on 2026-11-16, before any of these classes moved, with
code-plan/work/upgrade/make_job_pickle_samples.py; they hold every nested class those objects can
contain.

These tests pin two things: the samples still load into objects equal to ones built now, and the
classes they name are exactly the ones below. When the classes move, old names must keep loading
(the loader then maps them), and a class added to or removed from these objects shows up here.
"""
import pickle
from pathlib import Path

import pytest

from research_analysis.types import AnalysisResult, FlaggedClaim, LiteratureClaim, PossiblyIrrelevantClaim
from retrieval.ownership import OwnershipContributor
from retrieval.types import ContextBlock, ContextPack, QueryFacet

GOLDEN = Path(__file__).with_name("golden") / "old_job_pickles"

# The module paths and class names the samples store, as written before v2.00.
STORED_CLASSES = {
    ("retrieval.types", "ContextPack"),
    ("retrieval.types", "ContextBlock"),
    ("retrieval.types", "QueryFacet"),
    ("retrieval.ownership", "OwnershipContributor"),
    ("research_analysis.types", "AnalysisResult"),
    ("research_analysis.types", "LiteratureClaim"),
    ("research_analysis.types", "FlaggedClaim"),
    ("research_analysis.types", "PossiblyIrrelevantClaim"),
}


def expected_objects():
    """The same objects make_job_pickle_samples.py pickled, built with the current classes."""
    contributor = OwnershipContributor(subject_ref="paper:P001", data_ownership="user_owned",
                                       assertion_ref="assert:1", revision=2, basis_ref="basis:1",
                                       binding_digest="D" * 64)
    facet = QueryFacet(id="methods", query_terms=("method",), preferred_sections=("Methods",),
                       min_evidence_blocks=1)
    block = ContextBlock(content="The method uses a fixed sample.", source={"chunk_id": "P001#c1",
                         "paper_id": "P001", "field": "methods"}, weight=0.75, is_counterexample=False,
                         tokens=12, facet_ids=("methods",), ownership_contributors=(contributor,),
                         page_start=3, page_end=3)
    pack = ContextPack(blocks=(block,), estimated_tokens=12, token_hard_limit=4000,
                       counterexample_slots={"target": 1, "filled": 0}, truncated=False,
                       raw_recall_count=3, filtered_non_active_count=1, final_active_count=2,
                       query_facets=(facet,), coverage_by_facet={"methods": 1},
                       ownership_contributors=(contributor,))
    analysis = AnalysisResult(
        question="What sample does the method use?",
        literature_support=(LiteratureClaim(text="A fixed sample.", source_id={"chunk_id": "P001#c1",
                            "paper_id": "P001", "field": "methods"}, origin="model", facet_ids=("methods",)),),
        ai_general_knowledge=("General background.",),
        risks_and_uncertainties=("Small sample.",),
        recheck_locations=("P001 page 3",),
        flagged_unsourced=(FlaggedClaim(text="An unsourced sentence.", claimed_chunk_id="P999#c9"),),
        disclaimer="Suggestions, not conclusions.",
        analysis_model="sample-model",
        analyzed_at="2026-11-16T12:00",
        possibly_irrelevant=(PossiblyIrrelevantClaim(text="Another field.", source_id={"chunk_id": "P002#c4",
                             "paper_id": "P002", "field": None}, reason="different system"),),
        missing_facets=("results",),
    )
    return {"06_context_pack.pkl": pack, "07_analysis.pkl": analysis, "analysis_revision.pkl": analysis}


class RecordingUnpickler(pickle.Unpickler):
    """Loads like the job folder readers do and notes each (module, class) the data names."""

    def __init__(self, stream, seen):
        super().__init__(stream)
        self.seen = seen

    def find_class(self, module, name):
        self.seen.add((module, name))
        return super().find_class(module, name)


def load(path: Path, seen: set) -> object:
    with path.open("rb") as stream:
        return RecordingUnpickler(stream, seen).load()


@pytest.mark.parametrize("name", sorted(expected_objects()))
def test_an_old_job_pickle_loads_into_the_current_classes(name):
    assert load(GOLDEN / name, set()) == expected_objects()[name]


def test_the_samples_name_exactly_the_known_classes():
    seen = set()
    for name in expected_objects():
        load(GOLDEN / name, seen)
    assert seen == STORED_CLASSES
