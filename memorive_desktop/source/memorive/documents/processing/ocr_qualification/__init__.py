"""PAGE-RECOGNITION deterministic OCR and vision qualification primitives."""

from memorive.documents.processing.ocr_qualification.contracts import (
    OCRQualificationContractError,
    canonical_sha256,
    schema_hash,
    validate_normalized_page_transcription,
    validate_page_family_qualification,
    validate_page_subject_input,
)
from memorive.documents.processing.ocr_qualification.diagnostic import diagnose_page
from memorive.documents.processing.ocr_qualification.normalization import normalize_provider_payload, normalize_transcription
from memorive.documents.processing.ocr_qualification.reporting import compare_page, summarize_qualification_matrix
from memorive.documents.processing.ocr_qualification.routing import explain_route
from memorive.documents.processing.ocr_qualification.scoring import (
    QualificationThresholds,
    evaluate_accounting,
    project_family_qualification,
    score_page,
    validate_cell_denominator,
)

__all__ = [
    "OCRQualificationContractError",
    "QualificationThresholds",
    "canonical_sha256",
    "compare_page",
    "diagnose_page",
    "evaluate_accounting",
    "explain_route",
    "normalize_transcription",
    "normalize_provider_payload",
    "project_family_qualification",
    "schema_hash",
    "score_page",
    "summarize_qualification_matrix",
    "validate_cell_denominator",
    "validate_normalized_page_transcription",
    "validate_page_family_qualification",
    "validate_page_subject_input",
]
