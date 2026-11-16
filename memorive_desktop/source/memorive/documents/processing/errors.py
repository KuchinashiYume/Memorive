"""The document pipeline's errors. Callers catch them and show the message; each is logged at detail
level, with step and error, before it is raised."""
from __future__ import annotations


class DocumentProcessingError(Exception):
    """A pipeline error: a missing source file, no data ownership, a paper_id clash and the like."""


class EngineNotAvailable(DocumentProcessingError):
    """The conversion engine is not registered or not installed (marker, say); nothing falls back quietly."""


class OcrSafetyValve(DocumentProcessingError):
    """The input needs OCR (a picture or a scan) but OCR through the gateway is not ready. Nothing falls
    back to pytesseract, marker's own OCR or a model SDK."""


class ScanSuspectError(DocumentProcessingError):
    """A digital PDF gave too little text after conversion; it is probably a scan. An empty RawMD is not
    written; the file needs the OCR path."""


class TextIntegrityError(DocumentProcessingError):
    """The converted PDF text is badly damaged (U+FFFD). Such text is not passed on to cleaning,
    chunking, embedding and distillation."""


# ── embedding's errors ──────────────────────────────────────────────────────────────
class OwnershipMissingError(DocumentProcessingError):
    """Embedding: a chunk has no data_ownership. It is never sent to the cloud by default; a person decides."""


class OwnershipRouteError(DocumentProcessingError):
    """Embedding: data_ownership is not self or entrusted, or one paper's chunks differ, so the route is unclear."""


class EmbedLocalNotReadyError(DocumentProcessingError):
    """Embedding: entrusted data must be embedded locally and the local model is not ready. It is never
    sent to the cloud instead; carries the gateway's message about data ownership."""


class VectorModelConflictError(DocumentProcessingError):
    """Embedding: the collection already holds another embedding_model or vector_type. A new model
    means re-embedding the whole library, so old and new vectors are never mixed; checked before writing."""


class VectorDuplicateError(DocumentProcessingError):
    """Embedding: the chunk_id is already in the collection. Vectors are only added, never replaced
    (re-embedding uses a new collection or the re-embed flow). Chroma's add ignores a repeated id
    silently, hence this check."""


class VectorWriteVerifyError(DocumentProcessingError):
    """Embedding: after col.add, the count or metadata read back (run_id, model, vector_type) does not
    match the batch."""
