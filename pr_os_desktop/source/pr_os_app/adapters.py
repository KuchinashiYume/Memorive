from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from m11_log.product_paths import ProductPathLayout
from m8_state_machine import STATES, read_status

from .capabilities import build_capability_snapshot
from .job_store import SandboxJobStore


@dataclass(frozen=True)
class M8ReviewStatusAdapter:
    """Read-only compatibility adapter; job control never writes review_status."""

    def read(self, card_path: Path) -> str:
        status = read_status(card_path)
        if status not in STATES:
            raise ValueError("M8_REVIEW_STATUS_INVALID")
        return status


@dataclass(frozen=True)
class M9DeclaredCapabilityAdapter:
    """Consumes caller-declared metadata only; it never probes a provider or model."""

    revision: str
    created_at: str
    ttl_seconds: int = 60

    def snapshot(self, declarations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return build_capability_snapshot(declarations, revision=self.revision, created_at=self.created_at, ttl_seconds=self.ttl_seconds)


@dataclass(frozen=True)
class M11SandboxEvidenceAdapter:
    store: SandboxJobStore

    def integrity(self) -> dict[str, Any]:
        return self.store.integrity_report()


@dataclass(frozen=True)
class T10DirectoryRoleAdapter:
    layout: ProductPathLayout

    def doctor(self) -> dict[str, object]:
        return self.layout.doctor()


__all__ = ["M8ReviewStatusAdapter", "M9DeclaredCapabilityAdapter", "M11SandboxEvidenceAdapter", "T10DirectoryRoleAdapter"]
