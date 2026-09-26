"""Same-filesystem create-only Card visibility primitives."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .contracts import SegmentedDistillContractError


def write_staged_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return path


def publish_staged_card_no_clobber(staged_card: Path, target_card: Path) -> Path:
    """Atomically expose a complete staged file through an NTFS hard link.

    No copy/replace fallback is allowed: if the filesystem cannot provide the
    create-only hard-link primitive, publication fails closed.
    """

    staged = staged_card.resolve(strict=True)
    target_card.parent.mkdir(parents=True, exist_ok=True)
    target_parent = target_card.parent.resolve(strict=True)
    if staged.drive.casefold() != target_parent.drive.casefold():
        raise SegmentedDistillContractError(
            "CROSS_FILESYSTEM_PROMOTION_FORBIDDEN", f"{staged} -> {target_card}"
        )
    try:
        os.link(staged, target_card)
    except FileExistsError:
        raise
    except OSError as exc:
        raise SegmentedDistillContractError(
            "ATOMIC_NO_CLOBBER_PRIMITIVE_UNAVAILABLE", str(exc)
        ) from exc
    if target_card.read_bytes() != staged.read_bytes():
        raise SegmentedDistillContractError(
            "PUBLISHED_CARD_BYTES_MISMATCH", str(target_card)
        )
    return target_card


def publish_bytes_no_clobber(target_card: Path, payload: bytes) -> Path:
    staging = target_card.with_name(f".{target_card.name}.{uuid.uuid4().hex}.staged")
    write_staged_bytes(staging, payload)
    try:
        return publish_staged_card_no_clobber(staging, target_card)
    finally:
        staging.unlink(missing_ok=True)


def visibility_state(target_card: Path, admission_complete: bool) -> str:
    if not target_card.exists():
        return "RESUMABLE_CARD_NOT_VISIBLE"
    return "ADMITTED" if admission_complete else "PENDING_NOT_CONSUMABLE_BY_KNOWLEDGE_ADMISSION"


__all__ = [
    "publish_bytes_no_clobber",
    "publish_staged_card_no_clobber",
    "visibility_state",
    "write_staged_bytes",
]
