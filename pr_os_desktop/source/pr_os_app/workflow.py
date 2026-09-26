from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .contracts import canonical_sha256, immutable_copy
from .errors import ContractViolation


_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkflowDefinitionProvider:
    """Resolve immutable workflow definitions from one canonical directory."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def resolve(self, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id) or ".." in workflow_id:
            raise ContractViolation("WORKFLOW_ID_INVALID")
        unresolved = self.root / f"{workflow_id}.json"
        if unresolved.is_symlink():
            raise ContractViolation("WORKFLOW_PATH_SYMLINK_FORBIDDEN")
        candidate = unresolved.resolve()
        if not candidate.is_relative_to(self.root):
            raise ContractViolation("WORKFLOW_PATH_OUTSIDE_ROOT")
        try:
            raw = candidate.read_bytes()
        except FileNotFoundError as exc:
            raise ContractViolation("WORKFLOW_DEFINITION_NOT_FOUND") from exc
        try:
            definition = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractViolation("WORKFLOW_DEFINITION_INVALID_JSON") from exc
        if not isinstance(definition, dict) or definition.get("workflow_id") != workflow_id:
            raise ContractViolation("WORKFLOW_DEFINITION_ID_MISMATCH")
        frozen = immutable_copy(definition)
        return {
            "schema_version": "WorkflowDefinitionSnapshot-v1",
            "workflow_id": workflow_id,
            "definition": frozen,
            "definition_sha256": canonical_sha256(frozen),
            "source_file_sha256": hashlib.sha256(raw).hexdigest().upper(),
            "source_name": candidate.name,
        }


__all__ = ["WorkflowDefinitionProvider"]
