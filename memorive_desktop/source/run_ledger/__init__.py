"""DESKTOP-SERVICE desktop-runtime projection of the current RUNTIME_LOG writer surface.

The product runtime needs only the current errors and writer-lock modules.
Keeping this initializer narrow prevents import-time activation of unrelated
RETRIEVAL_WEIGHTING/KNOWLEDGE_ADMISSION/MODEL_GATEWAY/ARTIFACT_REGISTRY research and provider surfaces.
"""

from .errors import ConcurrentWriterError
from .writer_lock import KernelFileLock, KernelWriterLock, LockLease

__all__ = ["ConcurrentWriterError", "KernelFileLock", "KernelWriterLock", "LockLease"]
