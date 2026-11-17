"""Converting documents of other formats (Word, older Office files, images ...) for processing:
probing the format, converting, comparing candidates and diagnosing. The convert, clean and chunk
steps and the document structure contract use it.
"""
from memorive.documents.processing.document_conversion.facade import DocumentConversionFacade, compare_candidates, convert_document, diagnose_conversion
from memorive.documents.processing.document_conversion.models import (
    ConversionAmendment,
    ConversionCandidate,
    ConversionReceipt,
    ConversionResult,
    DocumentConversionError,
    ExecutionStatus,
    FormatVerdict,
    RecognitionStatus,
    SelectionDecision,
    SelectionStatus,
)
from memorive.documents.processing.document_conversion.registry import CANONICAL_FORMATS, probe_document

__all__ = [
    "CANONICAL_FORMATS",
    "ConversionAmendment",
    "ConversionCandidate",
    "ConversionReceipt",
    "ConversionResult",
    "DocumentConversionError",
    "DocumentConversionFacade",
    "ExecutionStatus",
    "FormatVerdict",
    "RecognitionStatus",
    "SelectionDecision",
    "SelectionStatus",
    "compare_candidates",
    "convert_document",
    "diagnose_conversion",
    "probe_document",
]
