"""The document pipeline: a source file becomes RawMD, CleanMD, chunks and vectors.

pipeline.py (run_document_processing, embed_from_chunks) is what the workflow (memorive_workflow)
calls for one paper. It chains the steps: convert (document_convert_convert with
document_conversion/), clean (document_clean_clean), chunk (document_chunk_chunk), embed
(document_embed_embed) and ingest (document_ingest_ingest); research_recommendations also calls the
step modules directly. A paper's file and folder names come from memorive/platform/naming.py, its
paper id registry from registration.py here.
document_structure/ reads a document's sections; the window reaches it through structure.* methods
(document_structure/desktop.py).

Not reached from the app's own code: python -m memorive.documents.processing.document_conversion.cli (probe,
verify-structure, convert); ocr_qualification/, which scores OCR output against reference pages; and
vector_generation.py, which builds and manages a separate Chroma collection. Tests call the last two, and
evidence_package.py, which reads a saved evidence package of this pipeline.
"""
from memorive.documents.processing.document_ingest_ingest import ingest
from memorive.documents.processing.document_convert_convert import convert
from memorive.documents.processing.document_clean_clean import clean
from memorive.documents.processing.document_chunk_chunk import chunk
from memorive.documents.processing.document_embed_embed import embed
from memorive.documents.processing.pipeline import (
    PipelineResult,
    embed_from_chunks,
    run_document_processing,
)

__all__ = [
    "ingest",
    "convert",
    "clean",
    "chunk",
    "embed",
    "run_document_processing",
    "embed_from_chunks",
    "PipelineResult",
]

# Names the package offered before; restored with the code behind them.
from memorive.documents.processing.pipeline import re_embed_from_rawmd
from memorive.documents.processing.pipeline import resume_after_note_review
from memorive.documents.processing.pipeline import run_pipeline
__all__ = [*__all__, 'run_pipeline', 'resume_after_note_review', 're_embed_from_rawmd']
