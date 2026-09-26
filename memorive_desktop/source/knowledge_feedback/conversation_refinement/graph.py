"""Deterministic provider export to normalized conversation graph mapping."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from knowledge_feedback.canonical import (
    make_hashed_payload,
    typed_payload_hash,
    validate_hash_descriptor,
    verify_hashed_payload,
)

from .contracts import BLOCK_TYPES, ROLES, ConversationContractError, object_ref, validate_export_snapshot
from .provider_adapter import validate_provider_export


_GRAPH_KEYS = {
    "schema_version",
    "graph_id",
    "revision",
    "snapshot_ref",
    "snapshot_hash",
    "conversation_ids",
    "nodes",
    "orphan_message_ids",
    "edge_policy",
    "deterministic",
    "content_hash",
}
_NODE_KEYS = {
    "message_id",
    "conversation_id",
    "parent_id",
    "role",
    "blocks",
    "created_at",
    "status",
    "edit_of",
    "regeneration_group",
    "provider_sequence",
}
_BLOCK_KEYS = {"type", "text", "metadata"}


def _validate_node(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConversationContractError(f"{field.upper()}_MUST_BE_OBJECT")
    node = deepcopy(dict(raw))
    if set(node) != _NODE_KEYS:
        raise ConversationContractError("GRAPH_NODE_EXACT_KEYS_MISMATCH")
    for key in ("message_id", "conversation_id", "created_at", "status"):
        if not isinstance(node[key], str) or not node[key]:
            raise ConversationContractError(f"GRAPH_NODE_{key.upper()}_INVALID")
    for key in ("parent_id", "edit_of", "regeneration_group"):
        if node[key] is not None and (not isinstance(node[key], str) or not node[key]):
            raise ConversationContractError(f"GRAPH_NODE_{key.upper()}_INVALID")
    if node["role"] not in ROLES:
        raise ConversationContractError("GRAPH_NODE_ROLE_INVALID")
    if isinstance(node["provider_sequence"], bool) or not isinstance(node["provider_sequence"], int) or node["provider_sequence"] < 0:
        raise ConversationContractError("GRAPH_NODE_SEQUENCE_INVALID")
    blocks = node["blocks"]
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence) or not blocks:
        raise ConversationContractError("GRAPH_NODE_BLOCKS_REQUIRED")
    for block in blocks:
        if not isinstance(block, Mapping) or set(block) != _BLOCK_KEYS:
            raise ConversationContractError("GRAPH_BLOCK_EXACT_KEYS_MISMATCH")
        if block["type"] not in BLOCK_TYPES:
            raise ConversationContractError("GRAPH_BLOCK_TYPE_INVALID")
        if block["text"] is not None and not isinstance(block["text"], str):
            raise ConversationContractError("GRAPH_BLOCK_TEXT_INVALID")
        if not isinstance(block["metadata"], Mapping):
            raise ConversationContractError("GRAPH_BLOCK_METADATA_INVALID")
    return node


def _cycle_nodes(nodes: Mapping[str, Mapping[str, Any]]) -> set[str]:
    cycle: set[str] = set()
    for start in nodes:
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in nodes:
            if current in positions:
                cycle.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = nodes[current]["parent_id"]
    return cycle


def validate_normalized_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(value, "normalized_conversation_graph")
    except Exception as exc:
        raise ConversationContractError("NORMALIZED_GRAPH_HASH_INVALID") from exc
    if set(result) != _GRAPH_KEYS:
        raise ConversationContractError("NORMALIZED_GRAPH_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != "CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V1":
        raise ConversationContractError("NORMALIZED_GRAPH_SCHEMA_UNSUPPORTED")
    if not isinstance(result["graph_id"], str) or not result["graph_id"]:
        raise ConversationContractError("GRAPH_ID_INVALID")
    if result["revision"] != 1:
        raise ConversationContractError("GRAPH_REVISION_INVALID")
    if not isinstance(result["snapshot_ref"], str) or not result["snapshot_ref"]:
        raise ConversationContractError("GRAPH_SNAPSHOT_REF_INVALID")
    validate_hash_descriptor(result["snapshot_hash"], "snapshot_hash")
    nodes = [_validate_node(item, f"nodes[{index}]") for index, item in enumerate(result["nodes"])]
    ids = [item["message_id"] for item in nodes]
    if ids != sorted(set(ids)):
        raise ConversationContractError("GRAPH_MESSAGE_IDS_MUST_BE_SORTED_UNIQUE")
    by_id = {item["message_id"]: item for item in nodes}
    if _cycle_nodes(by_id):
        raise ConversationContractError("GRAPH_CYCLE")
    orphans = sorted(
        item["message_id"]
        for item in nodes
        if item["parent_id"] is not None and item["parent_id"] not in by_id
    )
    if result["orphan_message_ids"] != orphans:
        raise ConversationContractError("GRAPH_ORPHAN_PROJECTION_DRIFT")
    conversations = sorted({item["conversation_id"] for item in nodes})
    if result["conversation_ids"] != conversations:
        raise ConversationContractError("GRAPH_CONVERSATION_PROJECTION_DRIFT")
    if result["edge_policy"] != "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE":
        raise ConversationContractError("GRAPH_EDGE_POLICY_DRIFT")
    if result["deterministic"] is not True:
        raise ConversationContractError("GRAPH_DETERMINISM_REQUIRED")
    return result


def build_normalized_graph(
    *, graph_id: str, snapshot: Mapping[str, Any], export: Mapping[str, Any]
) -> dict[str, Any]:
    checked_snapshot = validate_export_snapshot(snapshot)
    checked_export = validate_provider_export(export)
    if checked_snapshot["provider_profile"] != checked_export["provider_profile"]:
        raise ConversationContractError("GRAPH_PROVIDER_PROFILE_MISMATCH")
    if checked_snapshot["source_payload_hash"] != typed_payload_hash(checked_export):
        raise ConversationContractError("GRAPH_SNAPSHOT_PAYLOAD_HASH_MISMATCH")
    observed_denominator = {
        "conversation_count": len(checked_export["conversations"]),
        "message_count": sum(
            len(conversation["messages"])
            for conversation in checked_export["conversations"]
        ),
        "attachment_count": sum(
            len(conversation["attachments"])
            for conversation in checked_export["conversations"]
        ),
    }
    frozen_denominator = {
        key: checked_snapshot[key]
        for key in ("conversation_count", "message_count", "attachment_count")
    }
    if frozen_denominator != observed_denominator:
        raise ConversationContractError("GRAPH_SNAPSHOT_DENOMINATOR_MISMATCH")
    payload_bytes = len(
        json.dumps(
            checked_export,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if checked_snapshot["source_payload_bytes"] != payload_bytes:
        raise ConversationContractError("GRAPH_SNAPSHOT_PAYLOAD_BYTES_MISMATCH")
    nodes: list[dict[str, Any]] = []
    for conversation in checked_export["conversations"]:
        for sequence, raw in enumerate(conversation["messages"]):
            if not isinstance(raw, Mapping):
                raise ConversationContractError("MESSAGE_MUST_BE_OBJECT")
            expected = {
                "message_id",
                "parent_id",
                "role",
                "blocks",
                "created_at",
                "status",
                "edit_of",
                "regeneration_group",
            }
            if set(raw) != expected:
                raise ConversationContractError("MESSAGE_EXACT_KEYS_MISMATCH")
            node = deepcopy(dict(raw))
            node["conversation_id"] = conversation["conversation_id"]
            node["provider_sequence"] = sequence
            nodes.append(_validate_node(node, "message"))
    nodes.sort(key=lambda item: item["message_id"])
    by_id = {item["message_id"]: item for item in nodes}
    if len(by_id) != len(nodes):
        raise ConversationContractError("DUPLICATE_MESSAGE_ID")
    if _cycle_nodes(by_id):
        raise ConversationContractError("GRAPH_CYCLE")
    payload = {
        "schema_version": "CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V1",
        "graph_id": graph_id,
        "revision": 1,
        "snapshot_ref": object_ref(checked_snapshot, id_field="snapshot_id"),
        "snapshot_hash": deepcopy(checked_snapshot["content_hash"]),
        "conversation_ids": sorted({item["conversation_id"] for item in nodes}),
        "nodes": nodes,
        "orphan_message_ids": sorted(
            item["message_id"]
            for item in nodes
            if item["parent_id"] is not None and item["parent_id"] not in by_id
        ),
        "edge_policy": "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE",
        "deterministic": True,
    }
    return validate_normalized_graph(make_hashed_payload(payload))


def message_text(node: Mapping[str, Any]) -> str:
    checked = _validate_node(node, "node")
    return "\n".join(block["text"] for block in checked["blocks"] if block["type"] in {"text", "code"} and block["text"] is not None)


__all__ = ["build_normalized_graph", "message_text", "validate_normalized_graph"]
