"""P06/T05 governed document-conversion facade."""
from .facade import DocumentConversionFacade, compare_candidates, convert_document, diagnose_conversion
from .models import (
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
from .registry import CANONICAL_FORMATS, probe_document

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
