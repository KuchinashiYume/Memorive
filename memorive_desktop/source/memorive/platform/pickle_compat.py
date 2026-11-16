"""Reading pickles written by earlier versions after the classes they name have moved.

A job folder keeps 06_context_pack.pkl (a ContextPack) and 07_analysis.pkl (an AnalysisResult),
written by memorive/documents/workflow/executor.py, and an analysis revision keeps a pickled AnalysisResult
(memorive/documents/card_review/analysis_revision.py). A pickle names each class by module path and class name, so
a job folder from an earlier version names the places those classes had then. MOVED maps such an old
place to the class's place now; the three readers load through this module.
tests/characterize/test_old_job_pickles.py loads samples written before any of these classes moved.
"""
from __future__ import annotations

import io
import pickle
from typing import BinaryIO

# (old module, class name) -> (module now, class name now): the classes of a job folder's pickles
# moved in v2.00 from retrieval and research_analysis to memorive/research.
MOVED: dict[tuple[str, str], tuple[str, str]] = {
    ("retrieval.types", "ContextPack"): ("memorive.research.retrieval.types", "ContextPack"),
    ("retrieval.types", "ContextBlock"): ("memorive.research.retrieval.types", "ContextBlock"),
    ("retrieval.types", "QueryFacet"): ("memorive.research.retrieval.types", "QueryFacet"),
    ("retrieval.ownership", "OwnershipContributor"): ("memorive.research.retrieval.ownership", "OwnershipContributor"),
    ("research_analysis.types", "AnalysisResult"): ("memorive.research.analysis.types", "AnalysisResult"),
    ("research_analysis.types", "LiteratureClaim"): ("memorive.research.analysis.types", "LiteratureClaim"),
    ("research_analysis.types", "FlaggedClaim"): ("memorive.research.analysis.types", "FlaggedClaim"),
    ("research_analysis.types", "PossiblyIrrelevantClaim"): ("memorive.research.analysis.types", "PossiblyIrrelevantClaim"),
}


class JobUnpickler(pickle.Unpickler):
    """pickle.Unpickler that looks up a moved class at its new place."""

    def find_class(self, module: str, name: str):
        module, name = MOVED.get((module, name), (module, name))
        return super().find_class(module, name)


def load(stream: BinaryIO):
    return JobUnpickler(stream).load()


def loads(data: bytes):
    return JobUnpickler(io.BytesIO(data)).load()
