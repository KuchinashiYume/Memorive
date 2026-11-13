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
