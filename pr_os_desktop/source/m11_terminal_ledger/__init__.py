"""P08/T03 desktop-runtime projection of the current M11 writer surface.

The product runtime needs only the current errors and writer-lock modules.
Keeping this initializer narrow prevents import-time activation of unrelated
M7/M8/M9/M13 research and provider surfaces.
"""

from .errors import ConcurrentWriterError
from .writer_lock import KernelFileLock, KernelWriterLock, LockLease

__all__ = ["ConcurrentWriterError", "KernelFileLock", "KernelWriterLock", "LockLease"]
