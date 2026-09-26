"""Runner-supplied run closure and its persistent support-event form."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .errors import SchemaValidationError
from .schema import (
    ACCEPTANCE_VERDICTS,
    LIFECYCLE_STATUSES,
    SCHEMA_VERSION,
    validate_record,
)


@dataclass(frozen=True)
class RunTerminalEvent:
    """Append-only M11 support event; it is not a fourth business ledger."""

    record_id: str
    run_id: str
    lifecycle_status: str
    acceptance_verdict: str
    terminal_at: str
    source_evidence_refs: tuple[str, ...]
    supersedes_record_id: str | None = None

    def _payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "run_terminal_event",
            "record_id": self.record_id,
            "run_id": self.run_id,
            "lifecycle_status": self.lifecycle_status,
            "acceptance_verdict": self.acceptance_verdict,
            "terminal_at": self.terminal_at,
            "source_evidence_refs": list(self.source_evidence_refs),
            "supersedes_record_id": self.supersedes_record_id,
        }

    def validate(self) -> "RunTerminalEvent":
        validate_record(self._payload())
        return self

    def as_dict(self) -> dict:
        self.validate()
        return self._payload()


@dataclass(frozen=True)
class RunClosure:
    """Compatibility value object supplied by runner/supervisor, not inferred by M11."""

    run_id: str
    lifecycle_status: str
    acceptance_verdict: str
    closed_at: str
    source_evidence_refs: tuple[str, ...]

    def validate(self) -> "RunClosure":
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise SchemaValidationError("run_id must be a non-empty string")
        if self.lifecycle_status not in LIFECYCLE_STATUSES:
            raise SchemaValidationError(
                f"lifecycle_status must be one of {LIFECYCLE_STATUSES}"
            )
        if self.acceptance_verdict not in ACCEPTANCE_VERDICTS:
            raise SchemaValidationError(
                f"acceptance_verdict must be one of {ACCEPTANCE_VERDICTS}"
            )
        if (
            self.lifecycle_status in {"aborted", "invalidated"}
            and self.acceptance_verdict != "NOT_ASSESSED"
        ):
            raise SchemaValidationError(
                "aborted/invalidated runs must use acceptance NOT_ASSESSED"
            )
        try:
            parsed = datetime.fromisoformat(self.closed_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise SchemaValidationError("closed_at must be ISO 8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SchemaValidationError("closed_at must include a timezone")
        if not self.source_evidence_refs or any(
            not isinstance(ref, str) or not ref.strip()
            for ref in self.source_evidence_refs
        ):
            raise SchemaValidationError(
                "source_evidence_refs must contain non-empty strings"
            )
        return self

    def as_dict(self) -> dict:
        self.validate()
        payload = asdict(self)
        payload["source_evidence_refs"] = list(self.source_evidence_refs)
        return payload

    def to_terminal_event(
        self,
        *,
        record_id: str,
        supersedes_record_id: str | None = None,
    ) -> RunTerminalEvent:
        self.validate()
        return RunTerminalEvent(
            record_id=record_id,
            run_id=self.run_id,
            lifecycle_status=self.lifecycle_status,
            acceptance_verdict=self.acceptance_verdict,
            terminal_at=self.closed_at,
            source_evidence_refs=self.source_evidence_refs,
            supersedes_record_id=supersedes_record_id,
        ).validate()
