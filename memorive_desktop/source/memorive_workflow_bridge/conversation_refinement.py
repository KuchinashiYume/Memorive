from __future__ import annotations
from memorive_settings.task_scheduling import scheduled

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import unicodedata

from jsonschema import Draft202012Validator

from knowledge_feedback.canonical import make_hashed_payload, typed_payload_hash
from knowledge_feedback.conversation_refinement import (
    make_model_refinement_items,
    validate_normalized_graph,
    validate_typed_refinement_item,
)
from knowledge_feedback.conversation_refinement.contracts import REFINEMENT_TYPES
from model_gateway.local_structured_chat import LocalStructuredChatAdapter


_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
_LOCAL_PROFILE = re.compile(r"^local:[0-9a-f]{24}$", re.IGNORECASE)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REQUEST_FIELDS = {
    "task_id",
    "logical_slot",
    "profile_ref",
    "explicit_user_action",
    "source_snapshot_sha256",
    "source",
    "messages",
    "review_required",
    "auto_ingest",
}
_ITEM_FIELDS = {
    "message_id",
    "candidate_text",
    "refinement_type",
}


class ConversationRefinementBridgeError(ValueError):
    """Stable fail-closed error raised by the product composition seam."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as error:
        raise ConversationRefinementBridgeError("REFINEMENT_JSON_INVALID") from error


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_text(value: Any, code: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ConversationRefinementBridgeError(code)
    accepted = value.strip()
    if not accepted or len(accepted) > maximum or "\x00" in accepted:
        raise ConversationRefinementBridgeError(code)
    return accepted


def _normalized_identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().removeprefix("models/")


def _validate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not _REQUEST_FIELDS <= set(value) or set(value) - _REQUEST_FIELDS - {"language_context"}:
        raise ConversationRefinementBridgeError("REFINEMENT_REQUEST_FIELDS_INVALID")
    request = _json_copy(dict(value))
    if request.get("language_context") is not None:
        from memorive_language import validate
        request["language_context"]=validate(request["language_context"])
    task_id = _require_text(request["task_id"], "REFINEMENT_TASK_ID_INVALID", 128)
    if not _SAFE_IDENTIFIER.fullmatch(task_id):
        raise ConversationRefinementBridgeError("REFINEMENT_TASK_ID_INVALID")
    if request["logical_slot"] != "conversation_refinement":
        raise ConversationRefinementBridgeError("REFINEMENT_LOGICAL_SLOT_INVALID")
    profile_ref = _require_text(
        request["profile_ref"], "REFINEMENT_PROFILE_REF_INVALID", 160
    )
    if request["explicit_user_action"] is not True:
        raise ConversationRefinementBridgeError("REFINEMENT_EXPLICIT_ACTION_REQUIRED")
    if request["review_required"] is not True or request["auto_ingest"] is not False:
        raise ConversationRefinementBridgeError("REFINEMENT_REVIEW_BOUNDARY_INVALID")
    snapshot = request["source_snapshot_sha256"]
    if not isinstance(snapshot, str) or not _HEX64.fullmatch(snapshot):
        raise ConversationRefinementBridgeError("REFINEMENT_SNAPSHOT_HASH_INVALID")
    if not isinstance(request["source"], Mapping) or not request["source"]:
        raise ConversationRefinementBridgeError("REFINEMENT_SOURCE_INVALID")
    raw_messages = request["messages"]
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ConversationRefinementBridgeError("REFINEMENT_MESSAGES_REQUIRED")
    messages: list[dict[str, str]] = []
    for raw in raw_messages:
        if not isinstance(raw, Mapping) or set(raw) != {"role", "content"}:
            raise ConversationRefinementBridgeError("REFINEMENT_MESSAGE_FIELDS_INVALID")
        role = raw.get("role")
        if role not in {"user", "assistant"}:
            raise ConversationRefinementBridgeError("REFINEMENT_MESSAGE_ROLE_INVALID")
        messages.append(
            {
                "role": role,
                "content": _require_text(
                    raw.get("content"), "REFINEMENT_MESSAGE_CONTENT_INVALID", 200_000
                ),
            }
        )
    request.update(
        task_id=task_id,
        profile_ref=profile_ref,
        source_snapshot_sha256=snapshot.upper(),
        messages=messages,
    )
    return request


def _selected_messages(
    messages: Sequence[Mapping[str, str]],
) -> list[tuple[int, dict[str, str]]]:
    # Capacity failures must surface from the execution channel; never silently
    # omit the beginning of a selected conversation or truncate one message.
    return [(index, dict(row)) for index, row in enumerate(messages)]


def _build_graph(request: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    selected = _selected_messages(request["messages"])
    conversation_id = "conversation:" + hashlib.sha256(
        _canonical_bytes(request["source"])
    ).hexdigest()[:24]
    nodes: list[dict[str, Any]] = []
    message_ids: list[str] = []
    prior: str | None = None
    for original_index, row in selected:
        message_id = f"message-{original_index:08d}"
        message_ids.append(message_id)
        nodes.append(
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "parent_id": prior,
                "role": row["role"],
                "blocks": [{"type": "text", "text": row["content"], "metadata": {}}],
                "created_at": f"SEQUENCE:{original_index:08d}",
                "status": "VISIBLE_COMPLETE",
                "edit_of": None,
                "regeneration_group": None,
                "provider_sequence": original_index,
            }
        )
        prior = message_id
    nodes.sort(key=lambda row: row["message_id"])
    graph = make_hashed_payload(
        {
            "schema_version": "CONVERSATION_REFINEMENT_NORMALIZED_CONVERSATION_GRAPH_V1",
            "graph_id": f"graph:{request['task_id']}",
            "revision": 1,
            "snapshot_ref": f"desktop-session-snapshot:{request['task_id']}@1",
            "snapshot_hash": typed_payload_hash(
                {"source_snapshot_sha256": request["source_snapshot_sha256"]}
            ),
            "conversation_ids": [conversation_id],
            "nodes": nodes,
            "orphan_message_ids": [],
            "edge_policy": "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE",
            "deterministic": True,
        }
    )
    return validate_normalized_graph(graph), message_ids


def _response_schema(*, message_ids: Sequence[str]) -> dict[str, Any]:
    item_base = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_ITEM_FIELDS),
        "properties": {
            "candidate_text": {"type": "string", "minLength": 1, "maxLength": 10_000},
            "refinement_type": {"enum": sorted(REFINEMENT_TYPES)},
        },
    }
    prefix_items: list[dict[str, Any]] = []
    for message_id in message_ids:
        row = deepcopy(item_base)
        row["properties"]["message_id"] = {"const": message_id}
        prefix_items.append(row)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "minItems": len(prefix_items),
                "maxItems": len(prefix_items),
                "prefixItems": prefix_items,
            },
        },
    }


def _validate_model_proposals(
    value: Any,
    *,
    message_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_OBJECT_REQUIRED")
    output = _json_copy(dict(value))
    if set(output) != {"items"}:
        raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_FIELDS_INVALID")
    items = output["items"]
    if not isinstance(items, list) or len(items) != len(message_ids):
        raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_ITEM_COUNT_INVALID")
    for item, message_id in zip(items, message_ids, strict=True):
        if not isinstance(item, Mapping) or set(item) != _ITEM_FIELDS:
            raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_ITEM_FIELDS_INVALID")
        if item["message_id"] != message_id:
            raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_ITEM_ORDER_INVALID")
        _require_text(
            item["candidate_text"], "REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID", 10_000
        )
        if item["refinement_type"] not in REFINEMENT_TYPES:
            raise ConversationRefinementBridgeError("REFINEMENT_PROPOSAL_TYPE_INVALID")
    Draft202012Validator(_response_schema(message_ids=message_ids)).validate(output)
    return items


def _formal_subject_output(
    value: Any,
    *,
    task_id: str,
    graph: Mapping[str, Any],
    message_ids: Sequence[str],
) -> dict[str, Any]:
    proposals = _validate_model_proposals(value, message_ids=message_ids)
    try:
        items = make_model_refinement_items(
            graph=graph,
            proposals=proposals,
            scope="SELECTED_SESSION_MESSAGES",
        )
        items = [validate_typed_refinement_item(item) for item in items]
    except Exception as error:
        raise ConversationRefinementBridgeError(
            str(getattr(error, "code", None) or error)
            or "FORMAL_REFINEMENT_ITEM_CONSTRUCTION_FAILED"
        ) from error
    return {
        "schema_version": "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1",
        "pack_id": task_id,
        "graph_hash": _json_copy(graph["content_hash"]),
        "items": items,
    }


def _render_prompt(
    template: str, graph: Mapping[str, Any], message_ids: Sequence[str], language_context=None
) -> str:
    payload = {
        **({"language_context":language_context} if language_context is not None else {}),
        "pack_id": graph["graph_id"].removeprefix("graph:"),
        "graph_hash": graph["content_hash"],
        "selected_message_ids": list(message_ids),
        "conversation": [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": "\n".join(
                    block["text"] or ""
                    for block in row["blocks"]
                    if block["type"] in {"text", "code"}
                ),
            }
            for row in graph["nodes"]
            if row["message_id"] in message_ids
        ],
        "review_required": True,
        "auto_ingest": False,
    }
    return template.rstrip() + "\n\nINPUT_JSON:\n" + json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class ExistingConversationRefinementChannel:
    """Thin refinement-contract composition over existing execution surfaces."""

    def __init__(
        self,
        *,
        local_model_registry: Any,
        local_adapter: Any | None = None,
        profile_executor: Any | None = None,
        prompt_path: Path | None = None,
    ):
        self._local_models = local_model_registry
        self._local_adapter = local_adapter or LocalStructuredChatAdapter()
        self._profile_executor = profile_executor
        self._prompt_path = Path(
            prompt_path
            or (
                Path(__file__).resolve().parents[1]
                / "model_gateway"
                / "prompts"
                / "conversation_refinement"
                / "v3.md"
            )
        ).resolve()

    def _local_profile(self, profile_ref: str) -> dict[str, Any]:
        try:
            projection = self._local_models.call("local_models.list", {})
        except Exception as error:
            raise ConversationRefinementBridgeError(
                "LOCAL_MODEL_REGISTRY_UNAVAILABLE"
            ) from error
        rows = (
            projection.get("recognized_models", [])
            if isinstance(projection, Mapping)
            else []
        )
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("profile_ref") == profile_ref
        ]
        if len(matches) != 1:
            raise ConversationRefinementBridgeError(
                "LOCAL_MODEL_PROFILE_NOT_CONFIGURED"
            )
        row = _json_copy(dict(matches[0]))
        if (
            row.get("connection_status") != "AVAILABLE"
            or row.get("execution_eligible") is not True
            or row.get("exact_identity_available") is not True
            or row.get("structured_chat_adapter")
            != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            or row.get("endpoint_kind") != "ollama"
            or not isinstance(row.get("model_digest"), str)
            or not _HEX64.fullmatch(row["model_digest"])
        ):
            raise ConversationRefinementBridgeError(
                "LOCAL_MODEL_NOT_EXECUTION_ELIGIBLE"
            )
        return row

    @staticmethod
    def _validate_local_receipt(
        receipt: Any, *, requested_model: str, model_digest: str
    ) -> dict[str, Any]:
        if not isinstance(receipt, Mapping):
            raise ConversationRefinementBridgeError(
                "LOCAL_REFINEMENT_RECEIPT_INVALID"
            )
        accepted = _json_copy(dict(receipt))
        if (
            accepted.get("schema_version")
            != "EvaluationAssetsLocalStructuredChatReceipt-v1"
            or accepted.get("status") != "PASS"
            or accepted.get("local_model_request_count") != 1
            or accepted.get("external_request_count") != 0
            or accepted.get("cloud_fallback_count") != 0
            or accepted.get("api_cost_cny") != 0.0
            or _normalized_identity(str(accepted.get("requested_model") or ""))
            != _normalized_identity(requested_model)
            or _normalized_identity(str(accepted.get("returned_model") or ""))
            != _normalized_identity(requested_model)
            or str(accepted.get("model_digest") or "").upper()
            != model_digest.upper()
        ):
            raise ConversationRefinementBridgeError(
                "LOCAL_REFINEMENT_RECEIPT_INVALID"
            )
        return accepted

    @scheduled('REFINEMENT')
    def execute(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = _validate_request(value)
        try:
            template = self._prompt_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConversationRefinementBridgeError(
                "REFINEMENT_PROMPT_UNAVAILABLE"
            ) from error
        if not template.strip():
            raise ConversationRefinementBridgeError("REFINEMENT_PROMPT_EMPTY")

        graph, message_ids = _build_graph(request)
        schema = _response_schema(message_ids=message_ids)
        prompt = _render_prompt(template, graph, message_ids,request.get("language_context"))
        if not _LOCAL_PROFILE.fullmatch(request["profile_ref"]) or self._profile_executor is not None:
            caller = getattr(self._profile_executor, "call", None)
            if not callable(caller):
                raise ConversationRefinementBridgeError(
                    "REFINEMENT_EXECUTION_CHANNEL_NOT_AVAILABLE"
                )
            try:
                raw_result = caller(
                    "settings.execute_structured_chat",
                    {
                        "profile_ref": request["profile_ref"],
                        "prompt": prompt,
                        "response_schema": schema,
                        "purpose": "conversation_refinement",
                        "task_id": request["task_id"],
                    },
                )
            except Exception as error:
                code = str(getattr(error, "code", None) or error)
                raise ConversationRefinementBridgeError(
                    code or "REFINEMENT_PROFILE_EXECUTION_FAILED"
                ) from error
            if not isinstance(raw_result, Mapping):
                raise ConversationRefinementBridgeError(
                    "REFINEMENT_PROFILE_EXECUTION_RESULT_INVALID"
                )
            result = _json_copy(dict(raw_result))
            if (
                result.get("schema_version")
                != "SettingsStructuredChatExecutionResult-v1"
                or result.get("status") != "PASS"
                or result.get("profile_ref") != request["profile_ref"]
                or not isinstance(result.get("requested_model"), str)
                or not result["requested_model"]
                or not isinstance(result.get("returned_model"), str)
                or not result["returned_model"]
                or result.get("external_model_calls") != (0 if _LOCAL_PROFILE.fullmatch(request["profile_ref"]) else 1)
                or result.get("external_network_calls") not in {0, 1}
                or result.get("provider_calls") not in {0, 1}
                or not isinstance(result.get("execution_receipt"), Mapping)
                or not result["execution_receipt"]
            ):
                raise ConversationRefinementBridgeError(
                    "REFINEMENT_PROFILE_EXECUTION_RESULT_INVALID"
                )
            subject = _formal_subject_output(
                result.get("response"),
                task_id=request["task_id"],
                graph=graph,
                message_ids=message_ids,
            )
            return {
                "profile_ref": request["profile_ref"],
                "requested_model": result["requested_model"],
                "returned_model": result["returned_model"],
                "subject_output": subject,
                "model_calls": 1,
                "external_model_calls": result["external_model_calls"],
                "external_network_calls": result["external_network_calls"],
                "provider_calls": result["provider_calls"],
                "execution_receipt": result["execution_receipt"],
            }
        profile = self._local_profile(request["profile_ref"])
        outcome = self._local_adapter.execute(
            prompt=prompt,
            schema=schema,
            binding={
                "schema_version": "EvaluationAssetsLocalStructuredChatBinding-v1",
                "endpoint": profile["chat_endpoint"],
                "requested_model": profile["model_name"],
                "expected_model_digest": profile["model_digest"].upper(),
                "think": False,
                "temperature": 0,
                "seed": int(
                    hashlib.sha256(request["task_id"].encode("utf-8")).hexdigest()[:8],
                    16,
                ),
                "num_ctx": 65_536,
                "num_predict": 8_192,
                "cloud_fallback": False,
            },
            purpose="conversation_refinement",
            timeout_seconds=3_600,
        )
        if not getattr(outcome, "success", False):
            code = (
                getattr(outcome, "error_code", None)
                or "LOCAL_REFINEMENT_EXECUTION_FAILED"
            )
            raise ConversationRefinementBridgeError(str(code))
        receipt = self._validate_local_receipt(
            getattr(outcome, "receipt", None),
            requested_model=profile["model_name"],
            model_digest=profile["model_digest"],
        )
        subject = _formal_subject_output(
            getattr(outcome, "response", None),
            task_id=request["task_id"],
            graph=graph,
            message_ids=message_ids,
        )
        return {
            "profile_ref": request["profile_ref"],
            "requested_model": profile["model_name"],
            "returned_model": profile["model_name"],
            "subject_output": subject,
            "model_calls": 1,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "provider_calls": 0,
            "execution_receipt": receipt,
        }


__all__ = [
    "ConversationRefinementBridgeError",
    "ExistingConversationRefinementChannel",
]
