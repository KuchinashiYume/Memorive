"""Atomic append-only Artifact Registry for the ARTIFACT-REGISTRY candidate."""

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
from .schema import (
    EVENT_SCHEMA_VERSION,
    canonical_json,
    new_event_id,
    sha256_file,
    validate_envelope,
    validate_event,
)

_YEAR_FILE = re.compile(r"^[0-9]{4}\.jsonl$")


def _system_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ArtifactRegistry:
    """One annual append-only event stream with effective read-only views.

    The immutable registration and later relation/locator events are the
    authority.  Effective envelopes are projections and are never written
    back over historical events.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        corruption_evidence_root: Path | str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.corruption_evidence_root = (
            Path(corruption_evidence_root)
            if corruption_evidence_root is not None
            else self.root / "_corruption_evidence"
        )
        self.clock = clock or _system_now

    def register(self, envelope: Mapping[str, Any]) -> str:
        """Register one immutable envelope, returning ``appended`` or ``noop``."""

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
                old_hash = existing["content_hash"]["value"]
                new_hash = candidate["content_hash"]["value"]
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
            event = self._make_event(
                "artifact_registered",
                candidate,
                recorded_at=candidate["registered_at"],
            )
            self._append_event_locked(event)
        return "appended"

    def read_events(self) -> list[dict[str, Any]]:
        """Read complete events; malformed bytes fail the whole registry closed."""

        events: list[dict[str, Any]] = []
        seen_event_ids: set[str] = set()
        seen_artifact_ids: set[str] = set()
        for path in self._year_files():
            raw = path.read_bytes()
            for event in self._read_year(path, raw):
                if event["event_id"] in seen_event_ids:
                    self._raise_corruption(
                        path,
                        raw,
                        f"duplicate physical event_id {event['event_id']!r}",
                    )
                seen_event_ids.add(event["event_id"])
                if event["event_type"] == "artifact_registered":
                    artifact_id = event["payload"]["artifact_id"]
                    if artifact_id in seen_artifact_ids:
                        self._raise_corruption(
                            path,
                            raw,
                            f"duplicate physical artifact registration {artifact_id!r}",
                        )
                    seen_artifact_ids.add(artifact_id)
                events.append(event)
        return copy.deepcopy(events)

    def artifacts(self, *, effective: bool = True) -> list[dict[str, Any]]:
        """Return immutable registrations or their append-only effective views."""

        base = [
            event["payload"]
            for event in self.read_events()
            if event["event_type"] == "artifact_registered"
        ]
        if not effective:
            return copy.deepcopy(base)
        return [self.get(item["artifact_id"], effective=True) for item in base]

    def get(self, artifact_id: str, *, effective: bool = True) -> dict[str, Any]:
        """Get one registration; later events are projected only when requested."""

        events = self.read_events()
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
        current = copy.deepcopy(registration)
        if not effective:
            return current
        for event in events:
            payload = event["payload"]
            if payload.get("artifact_id") != artifact_id:
                continue
            if event["event_type"] == "lineage_resolution":
                existing = {
                    canonical_json({"link": link})
                    for link in current["lineage"]["resolved_parent_links"]
                }
                for link in payload["resolved_parent_links"]:
                    key = canonical_json({"link": link})
                    if key not in existing:
                        current["lineage"]["resolved_parent_links"].append(
                            copy.deepcopy(link)
                        )
                        existing.add(key)
                resolved = set(payload["resolved_requirements"])
                current["lineage"]["unresolved_parent_requirements"] = [
                    item
                    for item in current["lineage"]["unresolved_parent_requirements"]
                    if item["requirement"] not in resolved
                ]
                current["parents"] = copy.deepcopy(
                    current["lineage"]["resolved_parent_links"]
                )
            elif event["event_type"] == "locator_updated":
                current["locator"] = copy.deepcopy(payload["locator"])
        validate_envelope(current)
        return current

    def append_lineage_resolution(
        self,
        artifact_id: str,
        *,
        resolved_parent_links: list[Mapping[str, Any]],
        resolved_requirements: list[str],
        evidence_refs: list[str],
    ) -> str:
        """Append newly proven lineage without rewriting the base Envelope."""

        current = self.get(artifact_id, effective=True)
        payload = {
            "artifact_id": artifact_id,
            "resolved_parent_links": [copy.deepcopy(dict(x)) for x in resolved_parent_links],
            "resolved_requirements": list(resolved_requirements),
            "evidence_refs": list(evidence_refs),
        }
        probe = self._make_event("lineage_resolution", payload)
        validate_event(probe)
        unresolved_names = {
            item["requirement"]
            for item in current["lineage"]["unresolved_parent_requirements"]
        }
        missing_requirements = set(resolved_requirements) - unresolved_names
        if missing_requirements:
            raise EnvelopeValidationError(
                "lineage resolution names requirements not currently unresolved: "
                + ", ".join(sorted(missing_requirements))
            )
        registered = {item["artifact_id"]: item for item in self.artifacts(effective=True)}
        for link in payload["resolved_parent_links"]:
            parent_id = link["parent_artifact_id"]
            if parent_id == artifact_id:
                raise EnvelopeValidationError("lineage resolution cannot self-reference")
            if parent_id not in registered:
                raise UnknownArtifactError(
                    f"lineage resolution parent is not registered: {parent_id}"
                )
            actual_hash = registered[parent_id]["content_hash"]["value"]
            if link["parent_content_hash"] != actual_hash:
                raise ArtifactConflictError(
                    f"parent hash mismatch for {parent_id}: "
                    f"registered={actual_hash}, supplied={link['parent_content_hash']}"
                )
        projected = copy.deepcopy(registered)
        child = projected[artifact_id]
        child["lineage"]["resolved_parent_links"].extend(
            copy.deepcopy(payload["resolved_parent_links"])
        )
        child["parents"] = copy.deepcopy(child["lineage"]["resolved_parent_links"])
        if self._find_cycles(projected):
            raise EnvelopeValidationError("lineage resolution would create a cycle")
        return self._append_domain_event(probe)

    def append_locator_update(
        self,
        artifact_id: str,
        *,
        new_path: Path | str,
        evidence_refs: list[str],
    ) -> str:
        """Move a locator without changing artifact identity or registered bytes."""

        current = self.get(artifact_id, effective=True)
        path = Path(new_path)
        if not path.is_file():
            raise ArtifactConflictError(f"new locator is not a file: {path}")
        expected_hash = current["content_hash"]["value"]
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ArtifactConflictError(
                "new locator bytes do not match registered artifact: "
                f"expected={expected_hash}, actual={actual_hash}"
            )
        event = self._make_event(
            "locator_updated",
            {
                "artifact_id": artifact_id,
                "content_hash": expected_hash,
                "locator": {"path": str(path)},
                "evidence_refs": list(evidence_refs),
            },
        )
        validate_event(event)
        return self._append_domain_event(event)

    def observe_knowledge_admission_state_transition(
        self,
        artifact_id: str,
        *,
        event_ref: str,
        from_status: str,
        to_status: str,
    ) -> str:
        """Record an KNOWLEDGE_ADMISSION-owned event reference without mutating Card bytes."""

        self.get(artifact_id, effective=False)
        event = self._make_event(
            "state_transition_observed",
            {
                "artifact_id": artifact_id,
                "state_owner": "KNOWLEDGE_ADMISSION",
                "event_ref": event_ref,
                "from_status": from_status,
                "to_status": to_status,
            },
        )
        validate_event(event)
        return self._append_domain_event(event)

    def state_events(self, artifact_id: str) -> list[dict[str, Any]]:
        self.get(artifact_id, effective=False)
        return [
            copy.deepcopy(event)
            for event in self.read_events()
            if event["event_type"] == "state_transition_observed"
            and event["payload"]["artifact_id"] == artifact_id
        ]

    def audit_integrity(self) -> list[dict[str, Any]]:
        """Detect missing/tampered files, bad parents, self-links, and cycles."""

        artifacts = {item["artifact_id"]: item for item in self.artifacts(effective=True)}
        issues: list[dict[str, Any]] = []
        for artifact_id, envelope in artifacts.items():
            path = Path(envelope["locator"]["path"])
            if not path.is_file():
                issues.append(
                    {
                        "code": "ARTIFACT_FILE_MISSING",
                        "artifact_id": artifact_id,
                        "locator": str(path),
                    }
                )
            else:
                actual_hash = sha256_file(path)
                expected_hash = envelope["content_hash"]["value"]
                if actual_hash != expected_hash:
                    issues.append(
                        {
                            "code": "ARTIFACT_HASH_MISMATCH",
                            "artifact_id": artifact_id,
                            "expected": expected_hash,
                            "actual": actual_hash,
                        }
                    )
            for link in envelope["lineage"]["resolved_parent_links"]:
                parent_id = link["parent_artifact_id"]
                if parent_id == artifact_id:
                    issues.append({"code": "SELF_PARENT", "artifact_id": artifact_id})
                elif parent_id not in artifacts:
                    issues.append(
                        {
                            "code": "PARENT_NOT_REGISTERED",
                            "artifact_id": artifact_id,
                            "parent_artifact_id": parent_id,
                        }
                    )
                elif (
                    artifacts[parent_id]["content_hash"]["value"]
                    != link["parent_content_hash"]
                ):
                    issues.append(
                        {
                            "code": "PARENT_HASH_MISMATCH",
                            "artifact_id": artifact_id,
                            "parent_artifact_id": parent_id,
                        }
                    )
        for cycle in self._find_cycles(artifacts):
            issues.append({"code": "LINEAGE_CYCLE", "artifact_ids": cycle})
        return sorted(issues, key=lambda x: canonical_json(x))

    def overwrite(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("overwrite is forbidden; append a registry event")

    def update(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("update is forbidden; append a registry event")

    def truncate(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise AppendOnlyViolation("truncate is forbidden for authoritative history")

    def _append_domain_event(self, event: Mapping[str, Any]) -> str:
        validate_event(event)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._single_writer_lock():
            for existing in self.read_events():
                if existing["event_type"] != event["event_type"]:
                    continue
                if canonical_json(existing["payload"]) == canonical_json(event["payload"]):
                    return "noop"
            self._append_event_locked(event)
        return "appended"

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
        validate_event(event)
        year = datetime.fromisoformat(
            event["recorded_at"].replace("Z", "+00:00")
        ).year
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
                for link in envelope["lineage"]["resolved_parent_links"]
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

