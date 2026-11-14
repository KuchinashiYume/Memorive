"""Append-only Markdown storage and search for DECISION_LOG DDL records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .schema import DECISION_ID_PATTERN, DECISION_STATUSES, DecisionValidationError, validate_decision


class AppendOnlyViolation(RuntimeError):
    """Raised when a caller attempts to overwrite or fork a decision chain."""


def render_markdown(record: Mapping[str, Any]) -> str:
    """Render JSON-compatible YAML frontmatter plus a human-readable body."""

    validated = validate_decision(record)
    metadata = json.dumps(validated, ensure_ascii=False, indent=2)
    alternatives = "\n".join(
        f"- **{item['alternative_id']}**: {item['summary']}"
        for item in validated["alternatives"]
    )
    rejections = "\n".join(
        f"- **{item['alternative_id']}**: {item['reason']}"
        for item in validated["rejection_reasons"]
    )
    return (
        f"---\n{metadata}\n---\n\n"
        f"# {validated['title']}\n\n"
        f"## 背景\n\n{validated['background']}\n\n"
        f"## 备选方案\n\n{alternatives}\n\n"
        f"## 最终决定\n\n{validated['final_decision']}\n\n"
        f"## 未选方案及原因\n\n{rejections}\n"
    )


def parse_markdown(text: str) -> dict[str, Any]:
    """Parse and validate one DDL Markdown file."""

    if not text.startswith("---\n"):
        raise DecisionValidationError("missing frontmatter start")
    try:
        raw_metadata, _body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise DecisionValidationError("missing frontmatter end") from exc
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise DecisionValidationError("frontmatter must be valid JSON/YAML") from exc
    return validate_decision(metadata)


class DecisionStore:
    """A filesystem-backed, append-only decision version store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.decisions_root = self.root / "decisions"

    def _validate_id(self, decision_id: str) -> None:
        if not isinstance(decision_id, str) or not DECISION_ID_PATTERN.fullmatch(decision_id):
            raise DecisionValidationError("decision_id must match DDL-YYYY-MM-DD-NNN")

    def _decision_dir(self, decision_id: str) -> Path:
        self._validate_id(decision_id)
        return self.decisions_root / decision_id

    @staticmethod
    def _version_path(decision_dir: Path, version: int) -> Path:
        return decision_dir / f"v{version:04d}.md"

    def create(self, record: Mapping[str, Any]) -> Path:
        """Create version 1; refuse any existing decision directory."""

        candidate = dict(record)
        candidate["version"] = 1
        candidate["previous_version"] = None
        validated = validate_decision(candidate)
        decision_dir = self._decision_dir(validated["decision_id"])
        self.decisions_root.mkdir(parents=True, exist_ok=True)
        try:
            decision_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise AppendOnlyViolation(
                f"decision already exists; overwrite forbidden: {validated['decision_id']}"
            ) from exc
        path = self._version_path(decision_dir, 1)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_markdown(validated))
        return path

    def revise(
        self,
        record: Mapping[str, Any],
        *,
        expected_previous_version: int,
    ) -> Path:
        """Append the next version and reject stale writers or chain forks."""

        decision_id = str(record.get("decision_id", ""))
        latest = self.load_latest(decision_id)
        if latest["version"] != expected_previous_version:
            raise AppendOnlyViolation(
                f"stale version: expected {expected_previous_version}, latest is {latest['version']}"
            )
        candidate = dict(record)
        candidate["version"] = expected_previous_version + 1
        candidate["previous_version"] = expected_previous_version
        validated = validate_decision(candidate)
        path = self._version_path(
            self._decision_dir(decision_id), validated["version"]
        )
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(render_markdown(validated))
        except FileExistsError as exc:
            raise AppendOnlyViolation(f"version already exists: {path.name}") from exc
        return path

    def load_version(self, decision_id: str, version: int) -> dict[str, Any]:
        """Load a specific immutable version."""

        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise DecisionValidationError("version must be a positive integer")
        path = self._version_path(self._decision_dir(decision_id), version)
        if not path.is_file():
            raise KeyError(f"unknown decision version: {decision_id} v{version:04d}")
        return parse_markdown(path.read_text(encoding="utf-8"))

    def list_versions(self, decision_id: str) -> list[int]:
        """List the existing contiguous version numbers."""

        decision_dir = self._decision_dir(decision_id)
        if not decision_dir.is_dir():
            return []
        versions = sorted(int(path.stem[1:]) for path in decision_dir.glob("v[0-9][0-9][0-9][0-9].md"))
        if versions != list(range(1, len(versions) + 1)):
            raise AppendOnlyViolation(f"non-contiguous version chain: {decision_id}")
        return versions

    def load_latest(self, decision_id: str) -> dict[str, Any]:
        """Load the current version of a decision."""

        versions = self.list_versions(decision_id)
        if not versions:
            raise KeyError(f"unknown decision: {decision_id}")
        return self.load_version(decision_id, versions[-1])

    def search(
        self,
        *,
        decision_id: str | None = None,
        affected_module: str | None = None,
        status: str | None = None,
        current_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Search by exact ID, affected module, and status, returning citations."""

        if decision_id is not None:
            self._validate_id(decision_id)
        if status is not None and status not in DECISION_STATUSES:
            raise DecisionValidationError("illegal status filter")
        hits: list[dict[str, Any]] = []
        if not self.decisions_root.is_dir():
            return hits
        decision_dirs = (
            [self._decision_dir(decision_id)]
            if decision_id is not None
            else sorted(path for path in self.decisions_root.iterdir() if path.is_dir())
        )
        for decision_dir in decision_dirs:
            if not decision_dir.is_dir():
                continue
            versions = self.list_versions(decision_dir.name)
            if current_only and versions:
                versions = versions[-1:]
            for version in versions:
                record = self.load_version(decision_dir.name, version)
                if affected_module is not None and affected_module not in record["affected_modules"]:
                    continue
                if status is not None and record["status"] != status:
                    continue
                path = self._version_path(decision_dir, version)
                hits.append(
                    {
                        "record": record,
                        "citation": f"{path.as_posix()}#v{version:04d}",
                    }
                )
        return hits


