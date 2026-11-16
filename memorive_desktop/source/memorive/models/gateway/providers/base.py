"""What a provider adapter does. Each provider has one adapter that handles its authentication,
builds its requests, reads its replies and returns them in one common shape.
"""
from __future__ import annotations


class ProviderAdapter:
    def probe_model_capabilities(self, slot, *, timeout: int = 30) -> dict:
        """Make a metadata-only live request and return normalized capabilities.

        The request must not send user content.  Provider adapters decide which
        metadata endpoint and, if required, which frozen capability registry to
        use.  This makes the startup contract provider-neutral at Router level.
        """
        raise NotImplementedError

    def invoke(self, slot, payload: dict) -> dict:
        """Chat: one call for slot and payload; returns {task_type, provider, model, text, usage, raw}."""
        raise NotImplementedError

    def embed(self, slot, payload: dict) -> dict:
        """Embeddings for payload['input']; returns {task_type, provider, model, embeddings: [[...]],
        usage, raw}. raw is for the caller and tests only and never goes into a log."""
        raise NotImplementedError

    def rerank(self, slot, payload: dict) -> dict:
        """Rerank payload['documents'] against payload['query']; returns {task_type, provider, model,
        results: [{index, relevance_score}], usage, raw}, results in index order matching documents.
        raw never goes into a log."""
        raise NotImplementedError

    def ocr(self, slot, payload: dict) -> dict:
        """OCR: one page image to Markdown.

        payload reaches the adapter only after the data ownership check; it holds image_bytes,
        mime_type, page_number, source_sha256 and the versioned prompt. The reply must repeat the page
        number and source hash and give the reply's SHA-256, which document processing records.
        """
        raise NotImplementedError

    def healthcheck(self, slot) -> dict:
        """For local embedding only: whether the service is ready (GET /api/tags, say), without sending
        any text. Returns a dict with at least 'ready': bool; document embedding decides whether to stop."""
        raise NotImplementedError
