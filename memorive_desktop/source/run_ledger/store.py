"""Append-only annual JSONL with three business views and run support events."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, NoReturn

from .errors import (
    AppendOnlyViolation,
    ConcurrentWriterError,
    CorruptLedgerError,
    RecordConflictError,
    SchemaValidationError,
    TruncatedLedgerError,
)
from .schema import (
    SCHEMA_VERSION,
    canonical_json,
    record_type_of,
    record_year,
    validate_record,
)
from .writer_lock import KernelWriterLock

_YEAR_FILE = re.compile(r"^[0-9]{4}\.jsonl$")


class LedgerStore:
    """One physical annual stream, partitioned by record and ledger type.

    The authority is the JSONL stream, never an index or synthesized view.
    Writers coordinate through one OS kernel-lock abstraction.  Persistent
    LockLease metadata is evidence only; stale/unknown evidence is never
    silently deleted and requires the sandbox recovery contract.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        corruption_evidence_root: Path | str | None = None,
    ):
        self.root = Path(root)
        self.corruption_evidence_root = (
            Path(corruption_evidence_root)
            if corruption_evidence_root is not None
            else self.root / "_corruption_evidence"
        )

    def append(self, record: dict[str, Any]) -> str:
        """Append one canonical record, returning ``appended`` or ``noop``."""
        validate_record(record)
        if record.get("schema_version") != SCHEMA_VERSION:
            raise SchemaValidationError(
                "new appends require current schema_version; legacy v1 is read-only"
            )
        canonical = canonical_json(record)
        encoded = (canonical + "\n").encode("utf-8")
        year_path = self.root / f"{record_year(record):04d}.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)

        with self._single_writer_lock():
            existing_records = self.read_records()
            for existing in existing_records:
                if existing["record_id"] != record["record_id"]:
                    continue
                if canonical_json(existing) == canonical:
                    return "noop"
                raise RecordConflictError(
                    f"record_id conflict: {record['record_id']!r}; old record preserved"
                )
            if record_type_of(record) == "run_terminal_event":
                self._validate_run_terminal_append(record, existing_records)

            before = year_path.read_bytes() if year_path.exists() else b""
            with year_path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            after = year_path.read_bytes()
            if not after.startswith(before) or len(after) != len(before) + len(encoded):
                raise AppendOnlyViolation(
                    "append verification failed: authoritative prefix changed"
                )
        return "appended"

    def read_records(
        self,
        *,
        record_type: str | None = None,
        ledger_type: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read all complete records; any malformed tail fails the whole read."""
        records: list[dict[str, Any]] = []
        seen_ids: dict[str, str] = {}
        for path in self._year_files():
            for record in self._read_year(path):
                record_id = record["record_id"]
                canonical = canonical_json(record)
                if record_id in seen_ids:
                    reason = (
                        f"duplicate physical record_id {record_id!r}; "
                        "idempotent submission must not append a second line"
                    )
                    self._raise_corruption(path, path.read_bytes(), reason)
                seen_ids[record_id] = canonical
                if record_type is not None and record_type_of(record) != record_type:
                    continue
                if ledger_type is not None and record.get("ledger_type") != ledger_type:
                    continue
                if run_id is not None and record["run_id"] != run_id:
                    continue
                records.append(record)
        return records

    def view(
        self,
        ledger_type: str,
        *,
        run_id: str | None = None,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        """Return a read-only logical view without changing authoritative lines."""
        records = self.read_records(
            record_type="ledger_record", ledger_type=ledger_type, run_id=run_id
        )
        if include_history:
            return copy.deepcopy(records)
        if ledger_type == "attempt":
            return copy.deepcopy(
                [record for record in records if record["record_kind"] == "terminal"]
            )

        superseded = {
            record.get("supersedes_record_id")
            for record in records
            if record.get("supersedes_record_id")
        }
        return copy.deepcopy(
            [record for record in records if record["record_id"] not in superseded]
        )

    def attempts(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.view("attempt", run_id=run_id)

    def paper_outcomes(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.view("paper_outcome", run_id=run_id)

    def publications(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.view("publication", run_id=run_id)

    def run_terminal_events(
        self,
        *,
        run_id: str | None = None,
        include_history: bool = True,
    ) -> list[dict[str, Any]]:
        records = self.read_records(record_type="run_terminal_event", run_id=run_id)
        if include_history:
            return copy.deepcopy(records)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record["run_id"], []).append(record)
        return copy.deepcopy(
            [self._run_terminal_head(group) for group in grouped.values()]
        )

    def current_run_terminal(self, run_id: str) -> dict[str, Any]:
        events = self.read_records(record_type="run_terminal_event", run_id=run_id)
        if not events:
            raise SchemaValidationError(f"run {run_id!r} has no terminal event")
        return copy.deepcopy(self._run_terminal_head(events))

    def attempt_chain(self, attempt_id: str) -> list[dict[str, Any]]:
        """Expand the immutable terminal attempt and every later amendment."""
        records = self.read_records(ledger_type="attempt")
        return copy.deepcopy(
            [
                record
                for record in records
                if record.get("attempt_id") == attempt_id
                or record.get("amends_attempt_id") == attempt_id
            ]
        )

    def current_attempt(self, attempt_id: str) -> dict[str, Any]:
        """Synthesize current visible information while retaining raw-chain access."""
        chain = self.attempt_chain(attempt_id)
        terminals = [r for r in chain if r["record_kind"] == "terminal"]
        if len(terminals) != 1:
            raise SchemaValidationError(
                f"attempt {attempt_id!r} requires exactly one terminal record"
            )
        current = copy.deepcopy(terminals[0])
        amendment_ids: list[str] = []
        for amendment in chain:
            if amendment["record_kind"] != "amendment":
                continue
            amendment_ids.append(amendment["record_id"])
            for field, value in amendment["amendment_fields"].items():
                current[field] = copy.deepcopy(value)
        current["_amendment_record_ids"] = amendment_ids
        return current

    def _validate_run_terminal_append(
        self,
        record: dict[str, Any],
        existing_records: list[dict[str, Any]],
    ) -> None:
        events = [
            item
            for item in existing_records
            if record_type_of(item) == "run_terminal_event"
            and item["run_id"] == record["run_id"]
        ]
        supersedes = record.get("supersedes_record_id")
        if not events:
            if supersedes is not None:
                raise SchemaValidationError(
                    "first run terminal event cannot supersede an absent record"
                )
            return
        head = self._run_terminal_head(events)
        if supersedes != head["record_id"]:
            raise SchemaValidationError(
                "later run terminal event must supersede the current head"
            )

    @staticmethod
    def _run_terminal_head(events: list[dict[str, Any]]) -> dict[str, Any]:
        superseded = {
            event["supersedes_record_id"]
            for event in events
            if event.get("supersedes_record_id")
        }
        heads = [event for event in events if event["record_id"] not in superseded]
        if len(heads) != 1:
            raise SchemaValidationError(
                f"run terminal chain requires exactly one head, found {len(heads)}"
            )
        return heads[0]

    def overwrite(self, *_args, **_kwargs) -> NoReturn:
        raise AppendOnlyViolation("overwrite is forbidden; append an amendment")

    def update(self, *_args, **_kwargs) -> NoReturn:
        raise AppendOnlyViolation("update is forbidden; append an amendment")

    def truncate(self, *_args, **_kwargs) -> NoReturn:
        raise AppendOnlyViolation("truncate is forbidden for authoritative history")

    def _year_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            path
            for path in self.root.iterdir()
            if path.is_file() and _YEAR_FILE.fullmatch(path.name)
        )

    def _read_year(self, path: Path) -> list[dict[str, Any]]:
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            self._raise_corruption(
                path,
                raw,
                "truncated JSONL tail: non-empty ledger does not end with newline",
                truncated=True,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._raise_corruption(path, raw, f"invalid UTF-8: {exc}")
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            try:
                record = json.loads(line)
                validate_record(record)
            except (json.JSONDecodeError, SchemaValidationError) as exc:
                self._raise_corruption(
                    path, raw, f"invalid record at line {line_no}: {exc}"
                )
            records.append(record)
        return records

    def _raise_corruption(
        self,
        ledger_path: Path,
        raw: bytes,
        reason: str,
        *,
        truncated: bool = False,
    ) -> NoReturn:
        digest = hashlib.sha256(raw).hexdigest()
        self.corruption_evidence_root.mkdir(parents=True, exist_ok=True)
        evidence_path = self.corruption_evidence_root / f"{ledger_path.stem}-{digest}.json"
        evidence = {
            "schema_version": "2.0",
            "event": "RUNTIME_LOG_TERMINAL_LEDGER_CORRUPTION_DETECTED",
            "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ledger_path": str(ledger_path),
            "ledger_sha256": digest,
            "ledger_size_bytes": len(raw),
            "reason": reason,
            "original_preserved": True,
        }
        if not evidence_path.exists():
            with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(evidence, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        error_type = TruncatedLedgerError if truncated else CorruptLedgerError
        raise error_type(
            f"ledger rejected fail-closed: {reason}; evidence={evidence_path}",
            ledger_path=ledger_path,
            evidence_path=evidence_path,
        )

    @contextmanager
    def _single_writer_lock(self) -> Iterator[None]:
        with KernelWriterLock(
            self.root,
            owner_id=f"{type(self).__module__}.{type(self).__qualname__}",
        ):
            yield
