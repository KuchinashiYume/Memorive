from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime_log.product_paths import ProductPathLayout
from knowledge_admission import STATES, read_status

from .capabilities import build_capability_snapshot
from .job_store import SandboxJobStore


@dataclass(frozen=True)
class KnowledgeAdmissionReviewStatusAdapter:
    """Read-only compatibility adapter; job control never writes review_status."""

    def read(self, card_path: Path) -> str:
        status = read_status(card_path)
        if status not in STATES:
            raise ValueError("KNOWLEDGE_ADMISSION_REVIEW_STATUS_INVALID")
        return status


@dataclass(frozen=True)
class ModelGatewayDeclaredCapabilityAdapter:
    """Consumes caller-declared metadata only; it never probes a provider or model."""

    revision: str
    created_at: str
    ttl_seconds: int = 60

    def snapshot(self, declarations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return build_capability_snapshot(declarations, revision=self.revision, created_at=self.created_at, ttl_seconds=self.ttl_seconds)


@dataclass(frozen=True)
class RuntimeLogSandboxEvidenceAdapter:
    store: SandboxJobStore

    def integrity(self) -> dict[str, Any]:
        return self.store.integrity_report()


@dataclass(frozen=True)
class LoggingDirectoryRoleAdapter:
    layout: ProductPathLayout

    def doctor(self) -> dict[str, object]:
        return self.layout.doctor()


__all__ = ["KnowledgeAdmissionReviewStatusAdapter", "ModelGatewayDeclaredCapabilityAdapter", "RuntimeLogSandboxEvidenceAdapter", "LoggingDirectoryRoleAdapter"]
