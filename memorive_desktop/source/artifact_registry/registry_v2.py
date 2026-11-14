"""Append-only ARTIFACT_REGISTRY Artifact Registry v2 with read-only v1 compatibility."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn

from .errors import (
    AppendOnlyViolation,
    ArtifactConflictError,
    ConcurrentWriterError,
    CorruptRegistryError,
    EnvelopeValidationError,
    TruncatedRegistryError,
    UnknownArtifactError,
)
from .schema_v2 import (
    ENVELOPE_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    LEGACY_ENVELOPE_SCHEMA_VERSION,
    canonical_json,
    content_hash_value,
    new_event_id,
    resolved_parent_links,
    validate_envelope,
    validate_event,
    validate_readable_envelope,
)

_YEAR_FILE = re.compile(r"^[0-9]{4}\.jsonl$")


def _system_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ArtifactRegistry:
    """One append-only Registry whose new writes are v2-only."""

    def __init__(
        self,
        root: Path | str,
        *,
        corruption_evidence_root: Path | str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.corruption_evidence_root = Path(
            corruption_evidence_root or self.root.parent / "corruption-evidence"
        )
        self.clock = clock or _system_now

    def register(self, envelope: Mapping[str, Any]) -> str:
        """Register a v2 Envelope; historical v1 objects are never appended."""

        candidate = copy.deepcopy(dict(envelope))
        validate_envelope(candidate)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._single_writer_lock():
            events = self.read_events()
            for event in events:
                if event["event_type"] != "artifact_registered":
                    continue
                existing = event["payload"]
                if existing["artifact_id"] != candidate["artifact_id"]:
                    continue
                old_hash = content_hash_value(existing)
                new_hash = content_hash_value(candidate)
                if old_hash != new_hash:
                    raise ArtifactConflictError(
                        "artifact_id conflict with different content hash: "
                        f"{candidate['artifact_id']}; old={old_hash}, new={new_hash}"
                    )
                if canonical_json(existing) == canonical_json(candidate):
                    return "noop"
                raise ArtifactConflictError(
                    "artifact_id and hash match but immutable registration facts differ: "
                    f"{candidate['artifact_id']}"
                )
            registered = self._artifact_map(events, include_locations=False)
            self._validate_parent_targets(candidate, registered)
            projected = copy.deepcopy(registered)
            projected[candidate["artifact_id"]] = candidate
            if self._find_cycles(projected):
                raise EnvelopeValidationError("registration would create a lineage cycle")
            event = self._make_event(
                "artifact_registered",
                candidate,
                recorded_at=candidate["registered_at"],
            )
            self._append_event_locked(event)
        return "appended"

    def read_events(self) -> list[dict[str, Any]]:
        """Read v1/v2 history fail-closed; never normalize or rewrite old bytes."""

        events: list[dict[str, Any]] = []
        seen_event_ids: set[str] = set()
        registrations: dict[str, dict[str, Any]] = {}
        head_event_ids: dict[str, str] = {}
        head_envelopes: dict[str, dict[str, Any]] = {}
        for path in self._year_files():
            raw = path.read_bytes()
            for event in self._read_year(path, raw):
                event_id = event["event_id"]
                if event_id in seen_event_ids:
                    self._raise_corruption(path, raw, f"duplicate physical event_id {event_id!r}")
                seen_event_ids.add(event_id)
                event_type = event["event_type"]
                payload = event["payload"]
                if event_type == "artifact_registered":
                    artifact_id = payload["artifact_id"]
                    if artifact_id in registrations:
                        self._raise_corruption(
                            path,
                            raw,
                            f"duplicate physical artifact registration {artifact_id!r}",
                        )
                    registrations[artifact_id] = copy.deepcopy(payload)
                    head_envelopes[artifact_id] = copy.deepcopy(payload)
                    head_event_ids[artifact_id] = event_id
                elif event_type == "lineage_resolution_event":
                    artifact_id = payload["artifact_id"]
                    if artifact_id not in registrations:
                        self._raise_corruption(path, raw, "lineage event references unknown artifact")
                    if registrations[artifact_id]["schema_version"] != ENVELOPE_SCHEMA_VERSION:
                        self._raise_corruption(path, raw, "v2 lineage event targets legacy envelope")
                    if payload["prior_head_event_id"] != head_event_ids[artifact_id]:
                        self._raise_corruption(path, raw, "lineage head chain is discontinuous")
                    if content_hash_value(payload["new_envelope"]) != content_hash_value(
                        registrations[artifact_id]
                    ):
                        self._raise_corruption(path, raw, "lineage event changes artifact bytes")
                    head_envelopes[artifact_id] = copy.deepcopy(payload["new_envelope"])
                    head_event_ids[artifact_id] = event_id
                elif event_type == "artifact_location_event":
                    artifact_id = payload["artifact_id"]
                    if artifact_id not in registrations:
                        self._raise_corruption(path, raw, "location event references unknown artifact")
                    if payload["content_hash"] != content_hash_value(registrations[artifact_id]):
                        self._raise_corruption(path, raw, "location event content hash mismatch")
                elif event_type == "state_transition_observed":
                    if payload["artifact_id"] not in registrations:
                        self._raise_corruption(path, raw, "state event references unknown artifact")
                elif event_type in {"lineage_resolution", "locator_updated"}:
                    if payload["artifact_id"] not in registrations:
                        self._raise_corruption(path, raw, "legacy domain event references unknown artifact")
                events.append(event)
        return copy.deepcopy(events)

    def artifacts(self, *, effective: bool = True) -> list[dict[str, Any]]:
        events = self.read_events()
        artifact_ids = [
            event["payload"]["artifact_id"]
            for event in events
            if event["event_type"] == "artifact_registered"
        ]
        return [
            self._get_from_events(events, artifact_id, effective=effective)
            for artifact_id in artifact_ids
        ]

    def get(self, artifact_id: str, *, effective: bool = True) -> dict[str, Any]:
        return self._get_from_events(self.read_events(), artifact_id, effective=effective)

    def overwrite(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("overwrite is forbidden; append a registry event")

    def update(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("update is forbidden; append a registry event")

    def truncate(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("truncate is forbidden for authoritative history")

    def _get_from_events(
        self,
        events: list[Mapping[str, Any]],
        artifact_id: str,
        *,
        effective: bool,
    ) -> dict[str, Any]:
        registration = next(
            (
                event["payload"]
                for event in events
                if event["event_type"] == "artifact_registered"
                and event["payload"]["artifact_id"] == artifact_id
            ),
            None,
        )
        if registration is None:
            raise UnknownArtifactError(f"unknown artifact_id: {artifact_id}")
        if not effective:
            return copy.deepcopy(registration)
        current = copy.deepcopy(registration)
        for event in events:
            payload = event["payload"]
            if payload.get("artifact_id") != artifact_id:
                continue
            if event["event_type"] == "lineage_resolution_event":
                current = copy.deepcopy(payload["new_envelope"])
            elif event["event_type"] == "artifact_location_event":
                current["locator"] = copy.deepcopy(payload["locator"])
            elif event["event_type"] == "lineage_resolution":
                existing = {canonical_json(link) for link in resolved_parent_links(current)}
                for link in payload["resolved_parent_links"]:
                    if canonical_json(link) not in existing:
                        current["lineage"]["resolved_parent_links"].append(copy.deepcopy(link))
                        existing.add(canonical_json(link))
                resolved = set(payload["resolved_requirements"])
                current["lineage"]["unresolved_parent_requirements"] = [
                    value
                    for value in current["lineage"]["unresolved_parent_requirements"]
                    if value["requirement"] not in resolved
                ]
                current["parents"] = copy.deepcopy(current["lineage"]["resolved_parent_links"])
            elif event["event_type"] == "locator_updated":
                current["locator"] = copy.deepcopy(payload["locator"])
        validate_readable_envelope(current)
        return current

    def _lineage_head(
        self, events: list[Mapping[str, Any]], artifact_id: str
    ) -> tuple[dict[str, Any], str]:
        registration_event = next(
            (
                event
                for event in events
                if event["event_type"] == "artifact_registered"
                and event["payload"]["artifact_id"] == artifact_id
            ),
            None,
        )
        if registration_event is None:
            raise UnknownArtifactError(f"unknown artifact_id: {artifact_id}")
        current = copy.deepcopy(registration_event["payload"])
        head_id = registration_event["event_id"]
        for event in events:
            if event["event_type"] != "lineage_resolution_event":
                continue
            if event["payload"]["artifact_id"] != artifact_id:
                continue
            current = copy.deepcopy(event["payload"]["new_envelope"])
            head_id = event["event_id"]
        return current, head_id

    def _artifact_map(
        self, events: list[Mapping[str, Any]], *, include_locations: bool
    ) -> dict[str, dict[str, Any]]:
        artifact_ids = [
            event["payload"]["artifact_id"]
            for event in events
            if event["event_type"] == "artifact_registered"
        ]
        return {
            artifact_id: self._get_from_events(
                events, artifact_id, effective=include_locations
            )
            if include_locations
            else self._lineage_head(events, artifact_id)[0]
            for artifact_id in artifact_ids
        }

    @staticmethod
    def _validate_parent_targets(
        envelope: Mapping[str, Any], registered: Mapping[str, Mapping[str, Any]]
    ) -> None:
        for link in resolved_parent_links(envelope):
            parent_id = link["parent_artifact_id"]
            if parent_id not in registered:
                raise UnknownArtifactError(
                    f"parent_artifacts entry is not registered: {parent_id}"
                )
            actual_hash = content_hash_value(registered[parent_id])
            if actual_hash != link["parent_content_hash"]:
                raise ArtifactConflictError(
                    f"parent hash mismatch for {parent_id}: "
                    f"registered={actual_hash}, supplied={link['parent_content_hash']}"
                )

    def _make_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "event_id": new_event_id(),
            "event_type": event_type,
            "recorded_at": recorded_at or self.clock(),
            "payload": copy.deepcopy(dict(payload)),
        }
        validate_event(event)
        return event

    def _append_event_locked(self, event: Mapping[str, Any]) -> None:
        if event.get("event_schema_version") != EVENT_SCHEMA_VERSION:
            raise AppendOnlyViolation("new Registry writes must use event schema v2")
        validate_event(event)
        year = datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00")).year
        year_path = self.root / f"{year:04d}.jsonl"
        before = year_path.read_bytes() if year_path.exists() else b""
        encoded = (canonical_json(event) + "\n").encode("utf-8")
        temp_path = self.root / f".{year_path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temp_path.open("xb") as stream:
                stream.write(before)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            candidate = temp_path.read_bytes()
            if not candidate.startswith(before) or len(candidate) != len(before) + len(encoded):
                raise AppendOnlyViolation(
                    "atomic append verification failed: authoritative prefix changed"
                )
            os.replace(temp_path, year_path)
            if year_path.read_bytes() != candidate:
                raise AppendOnlyViolation("atomic replacement verification failed")
        finally:
            temp_path.unlink(missing_ok=True)

    def _year_files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            path
            for path in self.root.iterdir()
            if path.is_file() and _YEAR_FILE.fullmatch(path.name)
        )

    def _read_year(self, path: Path, raw: bytes) -> list[dict[str, Any]]:
        if raw and not raw.endswith(b"\n"):
            self._raise_corruption(
                path,
                raw,
                "truncated JSONL tail: non-empty registry does not end with newline",
                truncated=True,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._raise_corruption(path, raw, f"invalid UTF-8: {exc}")
        events: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            try:
                event = json.loads(line)
                validate_event(event)
            except (json.JSONDecodeError, EnvelopeValidationError) as exc:
                self._raise_corruption(
                    path, raw, f"invalid registry event at line {line_no}: {exc}"
                )
            events.append(event)
        return events

    def _raise_corruption(
        self,
        registry_path: Path,
        raw: bytes,
        reason: str,
        *,
        truncated: bool = False,
    ) -> NoReturn:
        digest = hashlib.sha256(raw).hexdigest()
        self.corruption_evidence_root.mkdir(parents=True, exist_ok=True)
        stem = f"{registry_path.stem}-{digest}"
        raw_path = self.corruption_evidence_root / f"{stem}.bin"
        evidence_path = self.corruption_evidence_root / f"{stem}.json"
        if not raw_path.exists():
            with raw_path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        evidence = {
            "schema_version": "1.0",
            "event": "ARTIFACT_REGISTRY_ARTIFACT_REGISTRY_CORRUPTION_DETECTED",
            "detected_at": self.clock(),
            "registry_path": str(registry_path),
            "registry_sha256": digest,
            "registry_size_bytes": len(raw),
            "reason": reason,
            "original_preserved": True,
            "raw_evidence_path": str(raw_path),
        }
        if not evidence_path.exists():
            with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(evidence, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        error_type = TruncatedRegistryError if truncated else CorruptRegistryError
        raise error_type(
            f"registry rejected fail-closed: {reason}; evidence={evidence_path}",
            registry_path=registry_path,
            evidence_path=evidence_path,
        )

    @contextmanager
    def _single_writer_lock(self) -> Iterator[None]:
        lock_path = self.root / ".writer.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ConcurrentWriterError(
                f"single-writer lock already exists: {lock_path}; inspect manually"
            ) from exc
        try:
            os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)

    @staticmethod
    def _find_cycles(artifacts: Mapping[str, Mapping[str, Any]]) -> list[list[str]]:
        graph = {
            artifact_id: [
                link["parent_artifact_id"]
                for link in resolved_parent_links(envelope)
                if link["parent_artifact_id"] in artifacts
            ]
            for artifact_id, envelope in artifacts.items()
        }
        state: dict[str, int] = {}
        stack: list[str] = []
        cycles: list[list[str]] = []

        def visit(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for parent in graph.get(node, []):
                if state.get(parent, 0) == 0:
                    visit(parent)
                elif state.get(parent) == 1:
                    start = stack.index(parent)
                    cycle = stack[start:] + [parent]
                    if cycle not in cycles:
                        cycles.append(cycle)
            stack.pop()
            state[node] = 2

        for artifact_id in graph:
            if state.get(artifact_id, 0) == 0:
                visit(artifact_id)
        return cycles
