"""Errors for the ARTIFACT-REGISTRY ARTIFACT_REGISTRY artifact registry candidate."""

from __future__ import annotations

from pathlib import Path


class ArtifactRegistryRegistryError(RuntimeError):
    """Base error for candidate registry operations."""


class ContractValidationError(ArtifactRegistryRegistryError):
    """A ANALYSIS-ADMISSION compatibility artifact violates its closed contract."""


class SandboxBoundaryViolation(ArtifactRegistryRegistryError):
    """A ANALYSIS-ADMISSION materialization write would escape its sandbox."""


class EnvelopeValidationError(ArtifactRegistryRegistryError):
    """An Artifact Envelope or relation event violates the ARTIFACT_REGISTRY contract."""


class AdapterError(ArtifactRegistryRegistryError):
    """A legacy file cannot be adapted without guessing or coercion."""


class FrontmatterParseError(AdapterError):
    """A Markdown file has missing or invalid YAML frontmatter."""


class ArtifactConflictError(ArtifactRegistryRegistryError):
    """One immutable artifact ID was presented with conflicting facts."""


class AppendOnlyViolation(ArtifactRegistryRegistryError):
    """An operation would overwrite, truncate, or rewrite registry history."""


class ConcurrentWriterError(ArtifactRegistryRegistryError):
    """The single-writer lock is already held and needs operator review."""


class CorruptRegistryError(ArtifactRegistryRegistryError):
    """The registry failed closed and preserved corruption evidence."""

    def __init__(
        self,
        message: str,
        *,
        registry_path: Path,
        evidence_path: Path,
    ) -> None:
        super().__init__(message)
        self.registry_path = registry_path
        self.evidence_path = evidence_path


class TruncatedRegistryError(CorruptRegistryError):
    """The registry has a non-empty JSONL tail without a final newline."""


class UnknownArtifactError(ArtifactRegistryRegistryError):
    """A requested artifact ID has no immutable registration event."""
