"""Local embedding models: LocalEmbeddingSeam runs one behind the execution core's process isolation.
memorive/documents/processing/vector_generation.py checks a model's compatibility manifest with it.

Importing this package performs no runtime discovery, process start, or network
request. It uses the execution core (model_gateway/execution_core) as it is.
"""

from memorive.models.gateway.local_embedding.seam import (
    LocalEmbeddingOutcome,
    LocalEmbeddingSeam,
    LocalSeamViolation,
    build_compatibility_manifest,
    validate_compatibility_manifest,
)

__all__ = [
    "LocalEmbeddingOutcome",
    "LocalEmbeddingSeam",
    "LocalSeamViolation",
    "build_compatibility_manifest",
    "validate_compatibility_manifest",
]
