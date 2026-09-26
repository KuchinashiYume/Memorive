"""DataContracts terminal-ledger domain errors."""

from __future__ import annotations

from pathlib import Path


class TerminalLedgerError(RuntimeError):
    """Base error for terminal-ledger operations."""


class RealPublicationForbidden(TerminalLedgerError):
    """ANALYSIS-ADMISSION preview code was asked to perform a real publication action."""


class SchemaValidationError(ValueError):
    """A record does not satisfy the frozen DataContracts schema."""


class AppendOnlyViolation(TerminalLedgerError):
    """A caller attempted to mutate or truncate authoritative history."""


class RecordConflictError(TerminalLedgerError):
    """The same record_id was submitted with different canonical content."""


class ConcurrentWriterError(TerminalLedgerError):
    """The explicit single-writer lock could not be acquired."""


class CorruptLedgerError(TerminalLedgerError):
    """The ledger cannot be trusted and was therefore rejected fail-closed."""

    def __init__(self, message: str, *, ledger_path: Path, evidence_path: Path):
        super().__init__(message)
        self.ledger_path = ledger_path
        self.evidence_path = evidence_path


class TruncatedLedgerError(CorruptLedgerError):
    """The JSONL file has a non-newline-terminated, potentially partial tail."""


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


class ChannelEvidenceError(TerminalLedgerError):
    """A Execution channel attempt violates the frozen ServiceContracts evidence contract."""

    def __init__(self, code: str, details: tuple[str, ...] | list[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")
