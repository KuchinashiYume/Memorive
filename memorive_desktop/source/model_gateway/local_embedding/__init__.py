"""LOCAL-RETRIEVAL provider-neutral local embedding seam.

Importing this package performs no runtime discovery, process start, or network
request. The seam composes the accepted DataContracts execution core without changing it.
"""

from .seam import (
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
