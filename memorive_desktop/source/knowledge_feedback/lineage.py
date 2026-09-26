"""Append-only lineage, supersession, revoke, quarantine and replay contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import make_hashed_payload, validate_hash_descriptor, verify_hashed_payload
from .errors import ContractRejected


_ENTRY_KEYS = {
    "node_ref",
    "revision",
    "content_hash",
    "parent_refs",
    "supersedes_ref",
    "ingest_key",
    "status",
}
_LEDGER_KEYS = {
    "schema_version",
    "ledger_id",
    "entries",
    "allowed_external_refs",
    "self_refs",
    "cycle_paths",
    "dangling_refs",
    "duplicate_ingest_keys",
    "append_only",
    "old_bytes_mutated",
    "valid",
    "issue_codes",
    "content_hash",
}
_PLAN_KEYS = {
    "schema_version",
    "plan_id",
    "action",
    "candidate_ref",
    "candidate_hash",
    "reason",
    "target_candidate_state",
    "target_artifact_state",
    "new_revision_required",
    "prior_bytes_mutated",
    "execution_authorized",
    "registry_write_authorized",
    "index_write_authorized",
    "replay_requires_fresh_source_snapshot",
    "test_only",
    "content_hash",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractRejected(f"{field} must be non-empty exact text")
    return value


def _refs(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractRejected(f"{field} must be an array")
    result = [_text(item, f"{field}[]") for item in value]
    if not allow_empty and not result:
        raise ContractRejected(f"{field} must not be empty")
    if result != sorted(set(result)):
        raise ContractRejected(f"{field} must be sorted and unique")
    return result


def _cycles(graph: Mapping[str, set[str]]) -> list[list[str]]:
    state: dict[str, int] = {}
    stack: list[str] = []
    found: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for parent in sorted(graph.get(node, set())):
            if state.get(parent, 0) == 0:
                visit(parent)
            elif state.get(parent) == 1:
                start = stack.index(parent)
                cycle = stack[start:] + [parent]
                found.add(tuple(cycle))
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            visit(node)
    return [list(item) for item in sorted(found)]


def _compute_findings(
    entries: Sequence[Mapping[str, Any]], allowed_external_refs: set[str]
) -> tuple[list[str], list[list[str]], list[str], list[str]]:
    nodes = {str(item.get("node_ref")) for item in entries}
    self_refs: set[str] = set()
    dangling: set[str] = set()
    graph: dict[str, set[str]] = {node: set() for node in nodes}
    ingest_counts: dict[str, int] = {}
    for item in entries:
        node = str(item.get("node_ref"))
        parents = list(item.get("parent_refs", []))
        supersedes = item.get("supersedes_ref")
        references = parents + ([] if supersedes is None else [supersedes])
        ingest_key = str(item.get("ingest_key"))
        ingest_counts[ingest_key] = ingest_counts.get(ingest_key, 0) + 1
        for ref in references:
            if ref == node:
                self_refs.add(node)
            elif ref in nodes:
                graph[node].add(ref)
            elif ref not in allowed_external_refs:
                dangling.add(str(ref))
    return (
        sorted(self_refs),
        _cycles(graph),
        sorted(dangling),
        sorted(key for key, count in ingest_counts.items() if count > 1),
    )


def validate_lineage_ledger(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "lineage_ledger")
    except Exception as exc:
        raise ContractRejected("LINEAGE_LEDGER_HASH_INVALID") from exc
    if set(result) != _LEDGER_KEYS:
        raise ContractRejected("LINEAGE_LEDGER_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_LINEAGE_SUPERSESSION_LEDGER_V1":
        raise ContractRejected("LINEAGE_LEDGER_SCHEMA_UNSUPPORTED")
    _text(result["ledger_id"], "ledger_id")
    allowed = _refs(result["allowed_external_refs"], "allowed_external_refs", allow_empty=True)
    entries = result["entries"]
    if not isinstance(entries, list) or not entries:
        raise ContractRejected("LINEAGE_LEDGER_ENTRIES_REQUIRED")
    node_refs: list[str] = []
    for ordinal, raw in enumerate(entries):
        if not isinstance(raw, Mapping) or set(raw) != _ENTRY_KEYS:
            raise ContractRejected("LINEAGE_LEDGER_ENTRY_SHAPE_INVALID")
        node_ref = _text(raw["node_ref"], f"entries[{ordinal}].node_ref")
        node_refs.append(node_ref)
        revision = raw["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ContractRejected("LINEAGE_LEDGER_REVISION_INVALID")
        try:
            validate_hash_descriptor(
                raw["content_hash"], f"entries[{ordinal}].content_hash"
            )
        except Exception as exc:
            raise ContractRejected("LINEAGE_LEDGER_CONTENT_HASH_INVALID") from exc
        _refs(raw["parent_refs"], f"entries[{ordinal}].parent_refs", allow_empty=True)
        if raw["supersedes_ref"] is not None:
            _text(raw["supersedes_ref"], f"entries[{ordinal}].supersedes_ref")
        _text(raw["ingest_key"], f"entries[{ordinal}].ingest_key")
        if raw["status"] not in {"candidate", "review_pending", "approved_for_controlled_ingest", "rejected", "quarantined", "revoked"}:
            raise ContractRejected("LINEAGE_LEDGER_STATUS_INVALID")
    if node_refs != sorted(set(node_refs)):
        raise ContractRejected("LINEAGE_LEDGER_NODE_REFS_NOT_SORTED_UNIQUE")
    for field in ("self_refs", "dangling_refs", "duplicate_ingest_keys", "issue_codes"):
        _refs(result[field], field, allow_empty=True)
    cycles = result["cycle_paths"]
    if not isinstance(cycles, list) or any(
        not isinstance(path, list) or len(path) < 2 or path[0] != path[-1]
        for path in cycles
    ):
        raise ContractRejected("LINEAGE_LEDGER_CYCLES_INVALID")
    if result["append_only"] is not True or result["old_bytes_mutated"] is not False:
        raise ContractRejected("LINEAGE_LEDGER_APPEND_ONLY_BOUNDARY_DRIFT")
    if not isinstance(result["valid"], bool):
        raise ContractRejected("LINEAGE_LEDGER_VALIDITY_TYPE_INVALID")
    expected_self, expected_cycles, expected_dangling, expected_duplicates = (
        _compute_findings(entries, set(allowed))
    )
    if result["self_refs"] != expected_self:
        raise ContractRejected("LINEAGE_LEDGER_SELF_REF_FINDINGS_DRIFT")
    if result["cycle_paths"] != expected_cycles:
        raise ContractRejected("LINEAGE_LEDGER_CYCLE_FINDINGS_DRIFT")
    if result["dangling_refs"] != expected_dangling:
        raise ContractRejected("LINEAGE_LEDGER_DANGLING_FINDINGS_DRIFT")
    if result["duplicate_ingest_keys"] != expected_duplicates:
        raise ContractRejected("LINEAGE_LEDGER_DUPLICATE_FINDINGS_DRIFT")
    expected_valid = not any(
        result[field]
        for field in ("self_refs", "cycle_paths", "dangling_refs", "duplicate_ingest_keys")
    )
    if result["valid"] != expected_valid:
        raise ContractRejected("LINEAGE_LEDGER_VALIDITY_DRIFT")
    expected_issues: list[str] = []
    if result["self_refs"]:
        expected_issues.append("LINEAGE_SELF_REFERENCE")
    if result["cycle_paths"]:
        expected_issues.append("LINEAGE_CYCLE")
    if result["dangling_refs"]:
        expected_issues.append("LINEAGE_DANGLING_REFERENCE")
    if result["duplicate_ingest_keys"]:
        expected_issues.append("DUPLICATE_INGEST_KEY")
    if result["issue_codes"] != sorted(expected_issues):
        raise ContractRejected("LINEAGE_LEDGER_ISSUE_SET_DRIFT")
    _ = allowed
    return result


def build_lineage_ledger(
    *,
    ledger_id: str,
    entries: Sequence[Mapping[str, Any]],
    allowed_external_refs: Sequence[str],
) -> dict[str, Any]:
    copied = [deepcopy(dict(item)) for item in entries]
    copied.sort(key=lambda item: item.get("node_ref", ""))
    allowed = set(allowed_external_refs)
    self_refs, cycles, dangling, duplicate_keys = _compute_findings(copied, allowed)
    issues: list[str] = []
    if self_refs:
        issues.append("LINEAGE_SELF_REFERENCE")
    if cycles:
        issues.append("LINEAGE_CYCLE")
    if dangling:
        issues.append("LINEAGE_DANGLING_REFERENCE")
    if duplicate_keys:
        issues.append("DUPLICATE_INGEST_KEY")
    return validate_lineage_ledger(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_LINEAGE_SUPERSESSION_LEDGER_V1",
                "ledger_id": ledger_id,
                "entries": copied,
                "allowed_external_refs": sorted(allowed),
                "self_refs": self_refs,
                "cycle_paths": cycles,
                "dangling_refs": dangling,
                "duplicate_ingest_keys": duplicate_keys,
                "append_only": True,
                "old_bytes_mutated": False,
                "valid": not issues,
                "issue_codes": sorted(issues),
            }
        )
    )


def validate_replay_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "knowledge_replay_plan")
    except Exception as exc:
        raise ContractRejected("REPLAY_PLAN_HASH_INVALID") from exc
    if set(result) != _PLAN_KEYS:
        raise ContractRejected("REPLAY_PLAN_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "KNOWLEDGE_FEEDBACK_KNOWLEDGE_REPLAY_PLAN_V1":
        raise ContractRejected("REPLAY_PLAN_SCHEMA_UNSUPPORTED")
    for field in ("plan_id", "candidate_ref", "reason"):
        _text(result[field], field)
    if result["action"] not in {"REVOKE", "QUARANTINE", "REPLAY"}:
        raise ContractRejected("REPLAY_PLAN_ACTION_INVALID")
    try:
        validate_hash_descriptor(result["candidate_hash"], "candidate_hash")
    except Exception as exc:
        raise ContractRejected("REPLAY_PLAN_CANDIDATE_HASH_INVALID") from exc
    expected_candidate = "revoked" if result["action"] == "REVOKE" else "quarantined"
    if result["action"] == "REPLAY":
        expected_candidate = "candidate"
    if result["target_candidate_state"] != expected_candidate:
        raise ContractRejected("REPLAY_PLAN_CANDIDATE_STATE_DRIFT")
    if result["target_artifact_state"] != "not_created":
        raise ContractRejected("REPLAY_PLAN_ARTIFACT_STATE_MUST_REMAIN_NOT_CREATED")
    for field, expected in (
        ("new_revision_required", True),
        ("prior_bytes_mutated", False),
        ("execution_authorized", False),
        ("registry_write_authorized", False),
        ("index_write_authorized", False),
        ("replay_requires_fresh_source_snapshot", True),
        ("test_only", True),
    ):
        if result[field] is not expected:
            raise ContractRejected(f"REPLAY_PLAN_{field.upper()}_DRIFT")
    return result


def build_replay_plan(
    *,
    plan_id: str,
    action: str,
    candidate_ref: str,
    candidate_hash: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    target = "revoked" if action == "REVOKE" else "quarantined"
    if action == "REPLAY":
        target = "candidate"
    return validate_replay_plan(
        make_hashed_payload(
            {
                "schema_version": "KNOWLEDGE_FEEDBACK_KNOWLEDGE_REPLAY_PLAN_V1",
                "plan_id": plan_id,
                "action": action,
                "candidate_ref": candidate_ref,
                "candidate_hash": deepcopy(dict(candidate_hash)),
                "reason": reason,
                "target_candidate_state": target,
                "target_artifact_state": "not_created",
                "new_revision_required": True,
                "prior_bytes_mutated": False,
                "execution_authorized": False,
                "registry_write_authorized": False,
                "index_write_authorized": False,
                "replay_requires_fresh_source_snapshot": True,
                "test_only": True,
            }
        )
    )


__all__ = [
    "build_lineage_ledger",
    "build_replay_plan",
    "validate_lineage_ledger",
    "validate_replay_plan",
]

