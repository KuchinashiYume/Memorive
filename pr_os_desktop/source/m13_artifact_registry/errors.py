"""Errors for the P02/T03 M13 artifact registry candidate."""

from __future__ import annotations

from pathlib import Path


class M13RegistryError(RuntimeError):
    """Base error for candidate registry operations."""


class ContractValidationError(M13RegistryError):
    """A P02/T04 compatibility artifact violates its closed contract."""


class SandboxBoundaryViolation(M13RegistryError):
    """A P02/T04 materialization write would escape its sandbox."""


class EnvelopeValidationError(M13RegistryError):
    """An Artifact Envelope or relation event violates the M13 contract."""


class AdapterError(M13RegistryError):
    """A legacy file cannot be adapted without guessing or coercion."""


class FrontmatterParseError(AdapterError):
    """A Markdown file has missing or invalid YAML frontmatter."""


class ArtifactConflictError(M13RegistryError):
    """One immutable artifact ID was presented with conflicting facts."""


class AppendOnlyViolation(M13RegistryError):
    """An operation would overwrite, truncate, or rewrite registry history."""


class ConcurrentWriterError(M13RegistryError):
    """The single-writer lock is already held and needs operator review."""


class CorruptRegistryError(M13RegistryError):
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


class UnknownArtifactError(M13RegistryError):
    """A requested artifact ID has no immutable registration event."""
