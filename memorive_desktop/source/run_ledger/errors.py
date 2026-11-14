"""DataContracts terminal-ledger domain errors."""

from __future__ import annotations

from pathlib import Path


class TerminalLedgerError(RuntimeError):
    """Base error for terminal-ledger operations."""


class SchemaValidationError(ValueError):
    """A record does not satisfy the frozen DataContracts schema."""


class AppendOnlyViolation(TerminalLedgerError):
    """A caller attempted to mutate or truncate authoritative history."""


class ConcurrentWriterError(TerminalLedgerError):
    """The explicit single-writer lock could not be acquired."""


class EvidenceConflictError(TerminalLedgerError):
    """Independent Core sources disagree on a normalized fact."""


class PaperOutcomeClosureGap(TerminalLedgerError):
    """At least one paper frozen in sample_manifest lacks a terminal outcome."""

    code = "PAPER_OUTCOME_CLOSURE_GAP"

    def __init__(self, missing_paper_ids: list[str]):
        self.missing_paper_ids = tuple(sorted(missing_paper_ids))
        super().__init__(f"{self.code}: {', '.join(self.missing_paper_ids)}")


class PolicyViolation(TerminalLedgerError):
    """A retry/repair action would erase or wash a prior business result."""
