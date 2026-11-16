"""The document pipeline: a source file becomes RawMD, CleanMD, chunks and vectors.

The workflow (memorive_workflow) runs these steps for each paper: convert (document_convert_convert,
document_conversion/), clean (document_clean_clean), chunk (document_chunk_chunk), embed
(document_embed_embed) and ingest (document_ingest_ingest); pipeline.py chains them for one file.
document_structure/ reads a document's sections; the window reaches it through structure.* methods.

Other entries: python -m document_processing.document_conversion.cli (probe, verify-structure,
convert); ocr_qualification/ scores OCR output against reference pages and is run by hand;
vector_generation.py builds and manages a separate Chroma collection for a caller.
"""
from .document_ingest_ingest import ingest
from .document_convert_convert import convert
from .document_clean_clean import clean
from .document_chunk_chunk import chunk
from .document_embed_embed import embed
from .pipeline import (
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
from .pipeline import re_embed_from_rawmd
from .pipeline import resume_after_note_review
from .pipeline import run_pipeline
__all__ = [*__all__, 'run_pipeline', 'resume_after_note_review', 're_embed_from_rawmd']
