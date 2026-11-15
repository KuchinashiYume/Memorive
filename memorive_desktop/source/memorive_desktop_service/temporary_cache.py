"""Keep the temporary cache within the size chosen in Settings ("maximum cache").

The temporary cache is the folder of staged batches: the copies made when files are picked for import. It is
the same folder the "clear temporary cache" button empties. Files that reached the inbox live elsewhere and
are never touched here.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from memorive_folder_management.policy import windows_io_path

SPARE_SECONDS = 60 * 60     # a batch staged within the last hour may still be on its way into the inbox


def limit(root: Path | str, limit_mb: int, *, now: float | None = None) -> dict[str, int]:
    """Remove the oldest staged batches until the cache is no larger than ``limit_mb``.

    Nothing happens unless ``limit_mb`` is a whole number in the range Settings accepts (128 to 102400).
    Batches younger than an hour are kept even when the cache is over the limit. A batch that cannot be
    removed is skipped; the next pass tries again.
    """
    result = {"bytes": 0, "removed": 0, "removed_bytes": 0}
    if isinstance(limit_mb, bool) or not isinstance(limit_mb, int) or not 128 <= limit_mb <= 102400:
        return result
    folder = windows_io_path(Path(root).resolve(strict=False))
    if not folder.is_dir():
        return result
    moment = time.time() if now is None else now
    batches = []
    for child in folder.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            size = sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
            batches.append((child.stat().st_mtime, size, child))
        except OSError:
            continue
    total = sum(size for _, size, _ in batches)
    for modified, size, child in sorted(batches, key=lambda batch: batch[0]):
        if total <= limit_mb * 1024 * 1024:
            break
        if moment - modified < SPARE_SECONDS:
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            continue
        total -= size
        result["removed"] += 1
        result["removed_bytes"] += size
    result["bytes"] = total
    return result


__all__ = ["limit"]
