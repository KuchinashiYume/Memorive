"""Job folders written by earlier versions keep pickles that name classes by module path.

A processed paper's job folder holds 06_context_pack.pkl (a ContextPack) and 07_analysis.pkl (an
AnalysisResult), written with pickle.HIGHEST_PROTOCOL by memorive/documents/workflow/executor.py; an analysis
revision keeps pickle.dumps of an AnalysisResult (memorive/documents/card_review/analysis_revision.py). The samples
in golden/old_job_pickles were written on 2026-11-16, before any of these classes moved: the objects
expected_objects() builds, made with the classes as they were then and pickled the way the writers do
(pickle.HIGHEST_PROTOCOL, 5, for the two job folder files; pickle.dumps with the default protocol, 4,
for the revision). They hold every nested class those objects can contain.

The readers load through memorive/platform/pickle_compat.py: executor.py with pickle_compat.load,
analysis_revision.py with pickle_compat.loads, and these tests call the same two functions. They pin
three things: the samples still load into objects equal to ones built now; the classes they name are
exactly the ones below; and each of those names is either still a class at that place or a key of
pickle_compat.MOVED. When a class moves, its old name goes into MOVED; a class added to or removed
from these objects shows up here.
"""
import importlib
from pathlib import Path

import pytest

from memorive.platform import pickle_compat
from memorive.research.analysis.types import AnalysisResult, FlaggedClaim, LiteratureClaim, PossiblyIrrelevantClaim
from memorive.research.retrieval.ownership import OwnershipContributor
from memorive.research.retrieval.types import ContextBlock, ContextPack, QueryFacet

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
    """The objects the samples were made from, built with the current classes."""
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


class RecordingUnpickler(pickle_compat.JobUnpickler):
    """The readers' unpickler, also noting each (module, class) the data names; only for listing names."""

    def __init__(self, stream, seen):
        super().__init__(stream)
        self.seen = seen

    def find_class(self, module, name):
        self.seen.add((module, name))
        return super().find_class(module, name)


def read_as_the_product_does(path: Path) -> object:
    if path.name == "analysis_revision.pkl":
        return pickle_compat.loads(path.read_bytes())
    with path.open("rb") as stream:
        return pickle_compat.load(stream)


@pytest.mark.parametrize("name", sorted(expected_objects()))
def test_an_old_job_pickle_loads_into_the_current_classes(name):
    assert read_as_the_product_does(GOLDEN / name) == expected_objects()[name]


def test_the_samples_name_exactly_the_known_classes():
    seen = set()
    for name in expected_objects():
        with (GOLDEN / name).open("rb") as stream:
            RecordingUnpickler(stream, seen).load()
    assert seen == STORED_CLASSES


@pytest.mark.parametrize("stored", sorted(STORED_CLASSES))
def test_each_stored_name_is_still_there_or_mapped(stored):
    module, name = stored
    if stored not in pickle_compat.MOVED:
        assert isinstance(getattr(importlib.import_module(module), name), type)
