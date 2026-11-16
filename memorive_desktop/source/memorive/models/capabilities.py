from __future__ import annotations

import unicodedata


CHAT = "CHAT"
EMBEDDING = "EMBEDDING"
RERANKER = "RERANKER"
VISION_OCR = "VISION_OCR"
MODEL_CAPABILITIES = frozenset({CHAT, EMBEDDING, RERANKER, VISION_OCR})


def _identity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def infer_api_model_capability(provider: object, model_name: object) -> str:
    """Classify known non-chat model families without freezing provider catalogs.

    Model availability is still decided by the provider's live ``/models``
    catalog.  This classifier only keeps models with different input/output
    contracts away from incompatible workflow nodes.
    """

    del provider
    model = _identity(model_name).strip("/")
    # Existing native /embeddings executor already supports this exact family.
    # Do not infer embedding support from a general model's VL/vision label.
    if model == "qwen/qwen3-vl-embedding-8b":
        return EMBEDDING
    if (
        model == "bge-m3"
        or model.startswith("bge-m3:")
        or model.endswith("/bge-m3")
        or "/bge-m3:" in model
    ):
        return EMBEDDING
    if "reranker" in model or model.endswith("/bce-reranker-base_v1"):
        return RERANKER
    if model.endswith("deepseek-ai/deepseek-ocr") or model.endswith("deepseek-ocr"):
        return VISION_OCR
    return CHAT


def workflow_node_accepts_capability(node_id: object, capability: object) -> bool:
    node = _identity(node_id)
    value = str(capability or "").strip().upper()
    if value not in MODEL_CAPABILITIES:
        return False
    if value == EMBEDDING:
        return node == "chunk_embedding"
    if value == RERANKER:
        return node == "context_pack"
    if value == VISION_OCR:
        return node == "ingest"
    return node in {
        "ingest",
        "card_distill",
        "transport_review",
        "analysis",
        "judgment_review",
        "context_pack",
        "logic_review",
    }


__all__ = [
    "CHAT",
    "EMBEDDING",
    "MODEL_CAPABILITIES",
    "RERANKER",
    "VISION_OCR",
    "infer_api_model_capability",
    "workflow_node_accepts_capability",
]
