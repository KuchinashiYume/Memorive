"""Sandbox-bound append-only store for P05/T03 channel attempt records."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from .channel_evidence import (
    AMENDMENT_KIND,
    TERMINAL_KIND,
    canonical_json,
    validate_channel_record,
)
from .errors import (
    AppendOnlyViolation,
    ChannelEvidenceError,
    RecordConflictError,
)
from .store import LedgerStore


SANDBOX_MARKER_NAME = ".pr-os-sandbox-root"
SANDBOX_MARKER_BYTES = b"P05_T03_SANDBOX_V1\n"


class ChannelLedgerStore(LedgerStore):
    """Single-writer channel ledger whose root must stay inside one sandbox."""

    def __init__(
        self,
        root: Path | str,
        *,
        sandbox_root: Path | str,
        corruption_evidence_root: Path | str | None = None,
    ):
        root_path = Path(root).resolve()
        self.sandbox_root = Path(sandbox_root).resolve()
        marker = self.sandbox_root / SANDBOX_MARKER_NAME
        try:
            marker_bytes = marker.read_bytes()
        except OSError as exc:
            raise ChannelEvidenceError(
                "CHANNEL_SANDBOX_MARKER_REQUIRED", [str(marker)]
            ) from exc
        if marker_bytes != SANDBOX_MARKER_BYTES:
            raise ChannelEvidenceError(
                "CHANNEL_SANDBOX_MARKER_INVALID", [str(marker)]
            )
        try:
            root_path.relative_to(self.sandbox_root)
        except ValueError as exc:
            raise ChannelEvidenceError(
                "CHANNEL_STORE_OUTSIDE_SANDBOX",
                [str(root_path), str(self.sandbox_root)],
            ) from exc
        if root_path == self.sandbox_root:
            raise ChannelEvidenceError("CHANNEL_STORE_ROOT_TOO_BROAD")
        corruption_root = (
            Path(corruption_evidence_root).resolve()
            if corruption_evidence_root is not None
            else root_path / "_corruption_evidence"
        )
        try:
            corruption_root.relative_to(self.sandbox_root)
        except ValueError as exc:
            raise ChannelEvidenceError("CORRUPTION_EVIDENCE_OUTSIDE_SANDBOX") from exc
        super().__init__(root_path, corruption_evidence_root=corruption_root)

    def append(self, record: dict[str, Any]) -> str:
        validate_channel_record(record)
        canonical = canonical_json(record)
        encoded = (canonical + "\n").encode("utf-8")
        year = _record_year(record)
        path = self.root / f"{year:04d}.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        with self._single_writer_lock():
            records = self.read_records()
            for existing in records:
                if existing["record_id"] != record["record_id"]:
                    continue
                if canonical_json(existing) == canonical:
                    return "noop"
                raise RecordConflictError(
                    f"record_id conflict: {record['record_id']!r}; old record preserved"
                )
            if record["record_kind"] == TERMINAL_KIND:
                self._validate_terminal_append(record, records)
            else:
                self._validate_amendment_append(record, records)
            before = path.read_bytes() if path.exists() else b""
            with path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            after = path.read_bytes()
            if not after.startswith(before) or len(after) != len(before) + len(encoded):
                raise AppendOnlyViolation("channel append verification failed")
        return "appended"

    def append_accounting_amendment(self, record: dict[str, Any]) -> str:
        if record.get("record_kind") != AMENDMENT_KIND:
            raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_KIND_REQUIRED")
        return self.append(record)

    def read_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in self._year_files():
            raw = path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                self._raise_corruption(path, raw, "truncated JSONL tail", truncated=True)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                self._raise_corruption(path, raw, f"invalid UTF-8: {exc}")
            for line_no, line in enumerate(text.splitlines(), start=1):
                try:
                    record = json.loads(line)
                    validate_channel_record(record)
                except (json.JSONDecodeError, ChannelEvidenceError) as exc:
                    self._raise_corruption(path, raw, f"invalid line {line_no}: {exc}")
                if record["record_id"] in seen:
                    self._raise_corruption(
                        path, raw, f"duplicate physical record_id {record['record_id']!r}"
                    )
                seen.add(record["record_id"])
                records.append(record)
        return records

    def attempts(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        return copy.deepcopy(
            [
                row
                for row in self.read_records()
                if row["record_kind"] == TERMINAL_KIND
                and (run_id is None or row["run_id"] == run_id)
            ]
        )

    def attempt_chain(self, attempt_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(
            [row for row in self.read_records() if row["attempt_id"] == attempt_id]
        )

    def current_attempt(self, attempt_id: str) -> dict[str, Any]:
        chain = self.attempt_chain(attempt_id)
        terminals = [row for row in chain if row["record_kind"] == TERMINAL_KIND]
        if len(terminals) != 1:
            raise ChannelEvidenceError("CHANNEL_ATTEMPT_TERMINAL_COUNT_INVALID")
        current = copy.deepcopy(terminals[0])
        amendment_ids: list[str] = []
        for row in chain:
            if row["record_kind"] != AMENDMENT_KIND:
                continue
            amendment_ids.append(row["record_id"])
            for key, value in row["amendment_fields"].items():
                current[key] = copy.deepcopy(value)
        current["_amendment_record_ids"] = amendment_ids
        return current

    def delete(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("delete is forbidden for authoritative history")

    def _validate_terminal_append(
        self, record: dict[str, Any], records: list[dict[str, Any]]
    ) -> None:
        retry_of = record["retry_of_attempt_id"]
        if retry_of is None:
            return
        predecessors = [
            item
            for item in records
            if item["record_kind"] == TERMINAL_KIND and item["attempt_id"] == retry_of
        ]
        if len(predecessors) != 1:
            raise ChannelEvidenceError("RETRY_PREDECESSOR_NOT_FOUND")
        predecessor = predecessors[0]
        if (
            predecessor["logical_operation_id"] != record["logical_operation_id"]
            or predecessor["attempt_no"] + 1 != record["attempt_no"]
        ):
            raise ChannelEvidenceError("RETRY_LINEAGE_MISMATCH")

    @staticmethod
    def _validate_amendment_append(
        record: dict[str, Any], records: list[dict[str, Any]]
    ) -> None:
        targets = [
            item
            for item in records
            if item["record_kind"] == TERMINAL_KIND
            and item["attempt_id"] == record["amends_attempt_id"]
        ]
        if len(targets) != 1:
            raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_TARGET_NOT_FOUND")
        target = targets[0]
        for key in (
            "run_id",
            "logical_operation_id",
            "attempt_no",
            "retry_of_attempt_id",
            "task_id",
            "stage",
            "paper_id",
        ):
            if record[key] != target[key]:
                raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_LINEAGE_MISMATCH", [key])
        if record["evidence_class"] != target["channel_evidence"]["evidence_class"]:
            raise ChannelEvidenceError("ACCOUNTING_AMENDMENT_CLASS_MISMATCH")

def _record_year(record: dict[str, Any]) -> int:
    parsed = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
    return parsed.year
