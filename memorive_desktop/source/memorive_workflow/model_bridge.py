from __future__ import annotations
from memorive_settings.call_ledger import call_scope, execution_checkpoint, ExecutionControlSignal

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
import uuid
import time

from model_gateway.errors import GatewayError, InvalidHealthcheckTarget, OcrResultInvalid

from .capacity import describe_capacity


PURPOSE = "core_document_processing"

_TASK_NODE = {
    "distill": "03_CARD_DISTILL",
    "verify": "04_CARD_CROSS_CHECK",
    "analysis": "07_ANALYSIS",
    "verify_judgment": "08_JUDGMENT_CROSS_CHECK",
    "verify_judgment_fallback": "08_JUDGMENT_CROSS_CHECK",
}
_EMBED_TASKS = frozenset({"embed_cloud", "embed_local"})
_SHA256 = re.compile(r"^[A-F0-9]{64}$")


def _provider_family(*values: Any) -> str | None:
    identity = " ".join(str(value or "") for value in values).casefold()
    families = (
        ("deepseek", ("deepseek",)),
        ("anthropic", ("anthropic", "claude")),
        ("openai", ("openai", "codex", "gpt-")),
        ("alibaba", ("alibaba", "dashscope", "qwen")),
        ("google", ("google", "gemini")),
        ("mistral", ("mistral",)),
        ("meta", ("meta", "llama")),
    )
    for family, markers in families:
        if any(marker in identity for marker in markers):
            return family
    return None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(_canonical_bytes(dict(value)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _bind_explicit_output_budget(
    execution_profile: Mapping[str, Any], maximum: Any
) -> dict[str, Any]:
    """Copy a profile and bind a real workflow max_tokens value to the wire."""
    bound = deepcopy(dict(execution_profile))
    if (
        isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and maximum > 0
        and isinstance(bound.get("model"), dict)
    ):
        # Capacity discovery may reduce an explicit workflow response budget;
        # it must never enlarge that budget before transport.
        bound["model"]["max_output_tokens_mode"] = "EXPLICIT"
    return bound


def _object(properties: Mapping[str, Any], *, required: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}

_DIRECT_VIOLATION = _object(
    {
        "rule": {"type": "string", "enum": [f"R{number}" for number in range(1, 10)]},
        "card_quote": _STRING,
        "source_quote": _STRING,
        "evidence_relation": {"type": "string", "enum": ["direct_conflict"]},
    }
)

_DIRECT_VIOLATIONS_SCHEMA = _object(
    {"violations": {"type": "array", "items": _DIRECT_VIOLATION}}
)

_BATCH_VIOLATION = _object(
    {
        "location": _STRING,
        "item_index": _INTEGER,
        "rule": {"type": "string", "enum": [f"R{number}" for number in range(1, 10)]},
        "card_quote": _STRING,
        "source_quote": _STRING,
        "evidence_relation": {"type": "string", "enum": ["direct_conflict"]},
    }
)

_BATCH_SCHEMA = _object(
    {
        "checked_locations": {"type": "array", "items": _STRING},
        "checked_items": {
            "type": "array",
            "items": _object(
                {
                    "location": _STRING,
                    "item_indexes": {"type": "array", "items": _INTEGER},
                }
            ),
        },
        "item_reviews": {
            "type": "array",
            "items": _object(
                {
                    "location": _STRING,
                    "item_index": _INTEGER,
                    "status": {"type": "string", "enum": ["supported", "violation"]},
                }
            ),
        },
        "violations": {"type": "array", "items": _BATCH_VIOLATION},
    }
)

_ANALYSIS_SCHEMA = _object(
    {
        "literature_support": {
            "type": "array",
            "items": _object(
                {
                    "text": _STRING,
                    "source_chunk_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                }
            ),
        },
        "possibly_irrelevant": {
            "type": "array",
            "items": _object(
                {
                    "text": _STRING,
                    "source_chunk_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}]
                    },
                    "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                }
            ),
        },
        "ai_general_knowledge": {"type": "array", "items": _STRING},
        "risks_and_uncertainties": {"type": "array", "items": _STRING},
        "recheck_locations": {"type": "array", "items": _STRING},
    }
)

_JUDGMENT_SCHEMA = _object(
    {
        "agrees": {"type": "boolean"},
        "disputes": {
            "type": "array",
            "items": _object(
                {
                    "type": {
                        "type": "string",
                        "enum": ["夸大", "曲解", "无依据", "超范围"],
                    },
                    "detail": _STRING,
                    "quote": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                }
            ),
        },
    }
)

# DeepSeek distillation uses several already-frozen Core response contracts
# (core card, key-data, auxiliary extraction and bounded repairs).  The prompt
# remains authoritative and the Core parser validates the exact shape.
_DISTILL_SCHEMA = {"type": "object"}
_TASK_SUGGESTED_OUTPUT_TOKENS = {
    # Suggestions remain visible planning inputs.  They are never used as an
    # artificial model ceiling by the embedding-recovery product runtime.
    "analysis": 65_536,
}
_CHANNEL_TIMEOUT_FLOORS = {
    "CLI": 300,
    "LOCAL": 600,
}


def _response_schema(task_type: str, prompt: str) -> Mapping[str, Any]:
    if task_type == "distill":
        return _DISTILL_SCHEMA
    if task_type == "analysis":
        return _ANALYSIS_SCHEMA
    if task_type in {"verify_judgment", "verify_judgment_fallback"}:
        return _JUDGMENT_SCHEMA
    if task_type == "verify":
        if "EVIDENCE_REVIEW_BATCH_INITIAL" in prompt or "EVIDENCE_REVIEW_BATCH_FINAL_REVIEW" in prompt:
            return _BATCH_SCHEMA
        return _DIRECT_VIOLATIONS_SCHEMA
    raise GatewayError(f"Core_TASK_NOT_MAPPED:{task_type}")


def _prompt_from_payload(payload: Mapping[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise GatewayError("Core_MESSAGES_MISSING")
    accepted: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise GatewayError("Core_MESSAGE_INVALID")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str) or not content.strip():
            raise GatewayError("Core_MESSAGE_INVALID")
        accepted.append((role, content))
    if len(accepted) == 1 and accepted[0][0] == "user":
        return accepted[0][1]
    return "\n\n".join(f"[{role}]\n{content}" for role, content in accepted)


class CoreModelBridge:
    """Bind Core's existing MODEL_GATEWAY seams to the immutable Desktop job snapshot."""

    def __init__(
        self,
        *,
        workflow_config: Mapping[str, Any],
        validation_runner: Any,
        evidence_root: Path | str,
        node_override_provider: Callable[[str], Mapping[str, Any]] | None = None,
    ):
        nodes = workflow_config.get("nodes")
        if not isinstance(nodes, Mapping):
            raise ValueError("Core_WORKFLOW_CONFIG_INVALID")
        self.nodes = {str(key): dict(value) for key, value in nodes.items() if isinstance(value, Mapping)}
        self.validation_runner = validation_runner
        self.node_override_provider = node_override_provider
        self.evidence_root = Path(evidence_root).resolve(strict=False)
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self._sequence = self._discover_sequence()
        self._totals = {
            "model_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "estimated_cost_unknown_count": 0,
        }

    def _discover_sequence(self) -> int:
        observed = []
        for path in self.evidence_root.glob("call-*.json"):
            match = re.fullmatch(r"call-(\d{6})\.(?:pre|post)\.json", path.name)
            if match:
                observed.append(int(match.group(1)))
        return max(observed, default=0)

    def node_enabled(self, node_id: str) -> bool:
        source = self.nodes.get(node_id)
        if not isinstance(source, dict):
            raise GatewayError(f"Core_NODE_DISABLED_OR_MISSING:{node_id}")
        node = deepcopy(source)
        if self.node_override_provider is not None:
            override = self.node_override_provider(node_id)
            if not isinstance(override, Mapping):
                raise GatewayError(f"Core_NODE_OVERRIDE_INVALID:{node_id}")
            node.update(override)
        if not isinstance(node.get("enabled"), bool):
            raise GatewayError(f"Core_NODE_ENABLED_INVALID:{node_id}")
        return node["enabled"]

    def _node(self, node_id: str) -> dict[str, Any]:
        source = self.nodes.get(node_id)
        node = deepcopy(source) if isinstance(source, dict) else source
        if isinstance(node, dict) and self.node_override_provider is not None:
            override = self.node_override_provider(node_id)
            if not isinstance(override, Mapping):
                raise GatewayError(f"Core_NODE_OVERRIDE_INVALID:{node_id}")
            node.update(deepcopy(dict(override)))
        if not isinstance(node, dict) or node.get("enabled") is not True:
            raise GatewayError(f"Core_NODE_DISABLED_OR_MISSING:{node_id}")
        profile_kind = node.get("profile_kind")
        execution_profile = node.get("execution_profile")
        if profile_kind not in {"API", "CLI", "LOCAL"} or not isinstance(execution_profile, Mapping):
            raise GatewayError(f"Core_NODE_EXECUTION_PROFILE_MISSING:{node_id}")
        service = execution_profile.get("service")
        model = execution_profile.get("model")
        requested_model = model.get("model_name") if isinstance(model, Mapping) else None
        if not isinstance(service, Mapping) or not isinstance(model, Mapping) or not isinstance(requested_model, str):
            raise GatewayError(f"Core_NODE_EXECUTION_PROFILE_INVALID:{node_id}")
        return node

    @staticmethod
    def _transient_retry_eligible(result: Mapping[str, Any]) -> bool:
        if result.get("status") == "PASS":
            return False
        receipt = result.get("execution_receipt")
        diagnostic = receipt.get("transport_diagnostic") if isinstance(receipt, Mapping) else None
        if isinstance(diagnostic, Mapping) and str(diagnostic.get("finish_reason") or "").lower() == "length":
            return False
        reason = str(result.get("reason") or "").upper()
        if reason == 'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN':
            # The inference transport is already closed. A fresh generation
            # may incur a second charge; it does not replay a tool action.
            # Preserve the uncertain attempt receipt and use only the saved
            # node retry budget. Never consume partial output as a success.
            return (result.get('status') == 'FAILED' and isinstance(diagnostic, Mapping)
                    and diagnostic.get('stream') is True and diagnostic.get('complete') is False
                    and diagnostic.get('exception_type') in {
                        'IncompleteRead', 'ConnectionResetError', 'RemoteDisconnected',
                        'ConnectionAbortedError', 'BrokenPipeError'})
        classification = (
            str(diagnostic.get("classification") or "").upper()
            if isinstance(diagnostic, Mapping)
            else ""
        )
        http_status = diagnostic.get("http_status") if isinstance(diagnostic, Mapping) else None
        if isinstance(http_status, str) and http_status.isascii() and http_status.isdecimal():
            http_status = int(http_status)
        if isinstance(http_status, int) and not isinstance(http_status, bool) and 400 <= http_status <= 599:
            if http_status == 400 and classification == "UNCLASSIFIED_RETRYABLE":
                return True
            # Structured transport evidence takes precedence over wrapper text.
            return http_status in {408, 429, 500, 502, 503, 504}
        if classification == "UNCLASSIFIED_RETRYABLE":
            return True
        if result.get("status") == "FAILED" and reason in {
            "STRUCTURED_CHAT_RESPONSE_INVALID", "STRUCTURED_CHAT_RESPONSE_EMPTY",
        }:
            # A finished generation can still contain malformed JSON. It has
            # produced no admitted artifact; regenerate within the user's saved
            # budget. Authentication/configuration and length handling above
            # remain authoritative, and every attempt keeps its own receipt.
            return True
        if result.get("status") == "FAILED" and reason == "API_RESPONSE_STATUS_UNKNOWN":
            return isinstance(diagnostic, Mapping) and diagnostic.get("exception_type") in {
                "RemoteDisconnected", "ConnectionResetError", "ConnectionAbortedError",
                "BrokenPipeError", "TimeoutError",
            }
        if any(marker in reason for marker in ("JSON_TRUNCATED", "RESPONSE_EMPTY", "SCHEMA", "PARSE")):
            return False
        return any(
            marker in reason
            for marker in (
                "TIMEOUT",
                "TEMPORAR",
                "CONNECTION",
                "NETWORK",
                "RATE_LIMIT",
                "HTTP_429",
                "HTTP_500",
                "HTTP_502",
                "HTTP_503",
                "HTTP_504",
                "HTTP_400_UNCLASSIFIED_RETRYABLE",
            )
        )

    @staticmethod
    def _wait_before_retry(retry_index: int) -> None:
        # Back off between attempts; remain responsive to pause/cancel/shutdown.
        deadline = time.monotonic() + min(2 ** retry_index, 30)
        while True:
            execution_checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.1))

    def select_judgment_candidates(self, producer_model: str | None):
        """Project the saved node-08 profile into Core's verifier selector.

        Core's default selector intentionally remains unchanged for legacy
        callers.  Desktop jobs must use their immutable workflow snapshot instead
        of consulting the legacy ``model_gateway/models.yaml`` a second time.
        """
        from evidence_review import VerifierCandidate

        node = self._node("08_JUDGMENT_CROSS_CHECK")
        execution_profile = node["execution_profile"]
        model = execution_profile["model"]
        requested_model = str(model.get("model_name") or "")
        producer_provider = _provider_family(producer_model)
        verifier_provider = _provider_family(
            node.get("provider"), requested_model, node.get("profile_ref")
        )
        if (
            producer_provider is None
            or verifier_provider is None
            or producer_provider == verifier_provider
        ):
            return producer_provider, []
        return producer_provider, [
            VerifierCandidate(
                task_type="verify_judgment",
                provider=verifier_provider,
                model_id=requested_model,
                enabled=True,
            )
        ]

    def review_identity(self):
        """Use this job's effective reviewer, never the legacy YAML slot."""
        observed = getattr(self, '_last_review_identity', None)
        if observed is not None:
            return observed
        node = self._node('04_CARD_CROSS_CHECK')
        model = node['execution_profile']['model']['model_name']
        return _provider_family(node.get('provider'), model, node.get('profile_ref')), model

    def _begin(self, *, capability: str, task_type: str, node_id: str, behavior: Mapping[str, Any], profile_source_node_id: str | None = None, effective_node: Mapping[str, Any] | None = None) -> tuple[int, Path]:
        execution_checkpoint()
        self._sequence += 1
        sequence = self._sequence
        node = effective_node if effective_node is not None else self._node(profile_source_node_id or node_id)
        execution_profile = node["execution_profile"]
        model = execution_profile["model"]
        pre = {
            "schema_version": "DesktopCoreModelCallBinding-v1",
            "sequence": sequence,
            "capability": capability,
            "task_type": task_type,
            "node_id": node_id,
            "profile_source_node_id": profile_source_node_id or node_id,
            "profile_ref": node.get("profile_ref"),
            "profile_kind": node["profile_kind"],
            "requested_model": model["model_name"],
            "behavior_sha256": _sha256(behavior),
            "behavior_content_recorded": False,
            "credential_value_recorded": False,
            "dispatch_state": "BOUND_BEFORE_SEND",
        }
        path = self.evidence_root / f"call-{sequence:06d}.pre.json"
        _atomic_json(path, pre)
        return sequence, path

    def _finish(
        self,
        sequence: int,
        *,
        result: Mapping[str, Any],
        retry_index: int = 0,
        retry_limit: int = 0,
        retry_context: Mapping[str, Any] | None = None,
    ) -> None:
        receipt = result.get("execution_receipt")
        post = {
            "schema_version": "DesktopCoreModelCallResult-v1",
            "sequence": sequence,
            "status": result.get("status"),
            "reason": result.get("reason"),
            "requested_model": result.get("requested_model"),
            "returned_model": result.get("returned_model"),
            "external_network_calls": int(result.get("external_network_calls") or 0),
            "external_model_calls": int(result.get("external_model_calls") or 0),
            "external_process_launches": int(result.get("external_process_launches") or 0),
            "execution_receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
            "retry": {
                "configured_retries": retry_limit,
                "attempt_index": retry_index,
                "consumed_retries": retry_index,
                "remaining_retries": max(0, retry_limit - retry_index),
                **dict(retry_context or {}),
            },
            "response_content_recorded": False,
            "credential_value_recorded": False,
        }
        _atomic_json(self.evidence_root / f"call-{sequence:06d}.post.json", post)
        self._totals["model_calls"] += 1
        for key in ("external_network_calls", "external_model_calls", "external_process_launches"):
            self._totals[key] += int(result.get(key) or 0)
        cost = receipt.get("estimated_cost_cny") if isinstance(receipt, Mapping) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self._totals["estimated_cost_cny"] += float(cost)
        else:
            self._totals["estimated_cost_unknown_count"] += 1

    def call(self, task_type: str, payload: dict, *, bypass_cache: bool = False) -> dict[str, Any]:
        del bypass_cache  # Desktop production calls never share Core's legacy response cache.
        node_id = _TASK_NODE.get(task_type)
        if node_id is None:
            raise GatewayError(f"Core_TASK_NOT_MAPPED:{task_type}")
        node = self._node(node_id)
        # A card repair uses the distillation model inside review node 04.
        # Keep model selection tied to its configured role while recording the
        # actual workflow node; model role is not an execution-lane identity.
        execution_node_id = getattr(self, "active_node_id", node_id)
        prompt = _prompt_from_payload(payload)
        schema = _response_schema(task_type, prompt)
        if payload.get("response_contract") is not None:
            if task_type == "analysis" and payload["response_contract"] == "analysis_targeted_revision_v1":
                from research_analysis.revision import REPAIR_SCHEMA
                schema = REPAIR_SCHEMA
            elif task_type == "distill" and payload["response_contract"] == "distill_aux_v1":
                from evidence_extraction.response_contracts import AUX_SCHEMA
                schema = AUX_SCHEMA
            elif task_type in {"verify_judgment", "verify_judgment_fallback"} and payload["response_contract"] == "judgment_source_language_v1":
                schema = deepcopy(_JUDGMENT_SCHEMA)
                schema['properties']['disputes']['items']['properties']['type']['enum'] = [
                    'overstatement', 'distortion', 'unsupported', 'out_of_scope',
                ]
            else:
                raise GatewayError("Core_RESPONSE_CONTRACT_INVALID")
        maximum = payload.get("max_tokens")
        execution_profile = _bind_explicit_output_budget(
            node["execution_profile"], maximum
        )
        # Bind reusable context to its content and role, independent of attempt paths.
        if node["profile_kind"] == "CLI" and execution_profile["service"].get("adapter_id") == "codex_cli":
            from memorive_settings.cache_session import REVISION, split_source
            pieces = split_source(prompt)
            if pieces is not None:
                from research_analysis.revision import REPAIR_SCHEMA
                contracts = [_ANALYSIS_SCHEMA, REPAIR_SCHEMA] if task_type == "analysis" else [_BATCH_SCHEMA, _DIRECT_VIOLATIONS_SCHEMA]
                execution_profile["model"].update(
                    prompt_cache_scope=_sha256({"source_prefix": pieces[0], "node_id": node_id}),
                    prompt_cache_session=REVISION,
                    prompt_cache_transport_schema=_object({"payload": {"anyOf": contracts}}),
                )
        requested_output_tokens = (
            maximum
            if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum > 0
            else _TASK_SUGGESTED_OUTPUT_TOKENS.get(task_type)
        )
        capacity = describe_capacity(
            profile_kind=node["profile_kind"],
            execution_profile=execution_profile,
            node_requested_output_tokens=requested_output_tokens,
        )
        effective_capacity = capacity.get("effective_capacity")
        resolved_output_tokens = (
            effective_capacity.get("output_tokens")
            if isinstance(effective_capacity, Mapping)
            else None
        )
        max_output_tokens = (
            None
            if node["profile_kind"] == "CLI"
            else (
                resolved_output_tokens
                if isinstance(resolved_output_tokens, int)
                and not isinstance(resolved_output_tokens, bool)
                and resolved_output_tokens > 0
                else None
            )
        )
        from model_gateway.resource_limits import resolve_call_limits

        limits = resolve_call_limits(
            profile_kind=node['profile_kind'],
            service=execution_profile['service'], model=execution_profile['model'],
            requested_output_tokens=requested_output_tokens,
        )
        max_output_tokens = limits['max_output_tokens']
        timeout_seconds = limits['timeout_seconds']
        # CLI profiles do not expose a per-model timeout in Settings.  Keep a
        # single stalled subprocess bounded so the existing per-block retry
        # policy can take over instead of leaving the whole task RUNNING.
        if node.get("profile_kind") == "CLI" and timeout_seconds is None:
            timeout_seconds = 300
        behavior_base = {
            "task_type": task_type,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper(),
            "response_schema_sha256": _sha256(schema),
            "max_output_tokens": max_output_tokens,
            "requested_output_tokens": requested_output_tokens,
            "output_budget_source": capacity.get("capacity_source"),
            "capacity": capacity,
            "timeout_seconds": timeout_seconds,
            "resource_limits": limits,
            "legacy_payload_timeout_not_applied": payload.get('timeout'),
        }
        retry_count = self._node(execution_node_id).get("retry_count", 0)
        if isinstance(retry_count, bool) or not isinstance(retry_count, int) or not 0 <= retry_count <= 10:
            raise GatewayError(f"Core_NODE_RETRY_COUNT_INVALID:{node_id}")
        result: dict[str, Any] = {}
        for retry_index in range(retry_count + 1):
            attempt_prompt = prompt
            if retry_index and result.get("reason") in {
                "STRUCTURED_CHAT_RESPONSE_INVALID", "STRUCTURED_CHAT_RESPONSE_EMPTY",
            }:
                attempt_prompt += (
                    "\n\nOutput-format correction: the previous generation was not a complete "
                    "parseable JSON object. Regenerate the full response from the same source "
                    "and required schema. Return only valid JSON with all brackets and quoted "
                    "strings closed. Do not invent missing source facts or relax evidence rules."
                )
            sequence, _path = self._begin(
                capability="STRUCTURED_CHAT",
                task_type=task_type,
                node_id=execution_node_id,
                profile_source_node_id=node_id,
                behavior={
                    **behavior_base,
                    "prompt_sha256": hashlib.sha256(attempt_prompt.encode("utf-8")).hexdigest().upper(),
                    "retry_index": retry_index,
                    "retry_limit": retry_count,
                },
            )
            try:
                with call_scope(job_id=self.evidence_root.parent.name, node_id=execution_node_id, profile_ref=node.get("profile_ref"), task_type=task_type):
                    result = dict(
                        self.validation_runner.execute_structured_chat(
                            profile_kind=node["profile_kind"],
                            service=execution_profile["service"],
                            model=execution_profile["model"],
                            prompt=attempt_prompt,
                            response_schema=schema,
                            purpose=PURPOSE,
                            max_output_tokens=max_output_tokens,
                            timeout_seconds=timeout_seconds,
                        )
                    )
            except ExecutionControlSignal as signal:
                self._finish(sequence, result={
                    'status': signal.state, 'reason': 'EXECUTION_'+signal.state,
                    'requested_model': execution_profile['model'].get('model_name'),
                    'execution_receipt': signal.execution_receipt,
                }, retry_index=retry_index, retry_limit=retry_count)
                raise
            except Exception as error:
                result = {
                    "status": "FAILED",
                    "reason": f"RUNNER_EXCEPTION:{type(error).__name__}:{error}",
                    "requested_model": execution_profile["model"].get("model_name"),
                    "returned_model": None,
                    "external_network_calls": 0,
                    "external_model_calls": 0,
                    "external_process_launches": 0,
                }
            receipt = result.get("execution_receipt")
            # DeepSeek's JSON mode guarantees a JSON object, not full JSON
            # Schema conformance.  Normal Analysis responses therefore need
            # the same local contract check as explicitly named response
            # contracts, while still failing closed on malformed content.
            if (
                result.get('status') == 'PASS'
                and (
                    task_type == 'analysis'
                    or payload.get('response_contract') is not None
                )
            ):
                import jsonschema
                try:
                    jsonschema.validate(result.get('response'), schema)
                except jsonschema.ValidationError as error:
                    result['status'] = 'FAILED'
                    result['reason'] = 'STRUCTURED_CHAT_RESPONSE_INVALID'
                    if isinstance(receipt, Mapping):
                        receipt = {**dict(receipt), 'status': 'FAILED',
                                   'failure_reason': (
                                       'Core_RESPONSE_CONTRACT_INVALID:'
                                       + str(error.validator)
                                       + ':'
                                       + '/'.join(str(part) for part in error.absolute_path)
                                   )}
            if isinstance(receipt, Mapping):
                result["execution_receipt"] = {
                    **dict(receipt),
                    "output_budget": {
                        "node_suggested_tokens": requested_output_tokens,
                        "effective_sent_tokens": (
                            receipt.get('output_budget', {}).get('effective_sent_tokens', max_output_tokens)
                        ),
                        "source": capacity.get("capacity_source"),
                        "profile_kind": node["profile_kind"],
                        "product_hard_limit_added": False,
                    },
                }
            self._finish(
                sequence,
                result=result,
                retry_index=retry_index,
                retry_limit=retry_count,
            )
            execution_checkpoint()
            if result.get("status") == "PASS" or not self._transient_retry_eligible(result):
                break
            if retry_index < retry_count:
                self._wait_before_retry(retry_index)
        if result.get("status") != "PASS" and task_type == "distill":
            receipt = result.get("execution_receipt")
            diagnostic = (
                receipt.get("transport_diagnostic")
                if isinstance(receipt, Mapping)
                else None
            )
            if (
                result.get("reason")
                in {"STRUCTURED_CHAT_RESPONSE_EMPTY", "STRUCTURED_CHAT_JSON_TRUNCATED"}
                and isinstance(diagnostic, Mapping)
                and diagnostic.get("finish_reason") == "length"
            ):
                usage = receipt.get("token_usage") if isinstance(receipt, Mapping) else {}
                return {
                    "text": "",
                    "model": str(
                        result.get("returned_model")
                        or result.get("requested_model")
                        or ""
                    ),
                    "provider": node.get("provider"),
                    "usage": dict(usage) if isinstance(usage, Mapping) else {},
                    "stop_reason": "length",
                    "transport": receipt.get("route") if isinstance(receipt, Mapping) else None,
                    "raw": {"choices": [{"finish_reason": "length"}]},
                    "execution_receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
                }
        if result.get("status") != "PASS" or not isinstance(result.get("response"), Mapping):
            raise GatewayError(f"Core_MODEL_CALL_FAILED:{task_type}:{result.get('reason')}")
        receipt = result.get("execution_receipt")
        usage = receipt.get("token_usage") if isinstance(receipt, Mapping) else {}
        returned_model = str(result.get("returned_model") or "")
        if task_type == 'verify':
            identity_model = returned_model or execution_profile['model']['model_name']
            self._last_review_identity = (
                _provider_family(node.get('provider'), identity_model, node.get('profile_ref')),
                identity_model,
            )
        response = dict(result["response"])
        return {
            "text": json.dumps(response, ensure_ascii=False, allow_nan=False),
            "model": returned_model,
            "provider": node.get("provider"),
            "usage": dict(usage) if isinstance(usage, Mapping) else {},
            "stop_reason": "stop",
            "transport": receipt.get("route") if isinstance(receipt, Mapping) else None,
            "raw": {"choices": [{"finish_reason": "stop"}]},
            "execution_receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
        }

    def embed(self, task_type: str, inputs: list[str]) -> dict[str, Any]:
        if task_type not in _EMBED_TASKS or not inputs:
            raise GatewayError(f"Core_EMBED_TASK_INVALID:{task_type}")
        node_id = getattr(self, 'active_node_id', '02_CHUNK_EMBEDDING')
        if node_id not in {'02_CHUNK_EMBEDDING', '06_CONTEXT_PACK'}:
            raise GatewayError('Core_EMBED_EXECUTION_NODE_INVALID')
        node = self._node(node_id)
        execution_profile = node["execution_profile"]
        behavior_base = {
            "task_type": task_type,
            "input_count": len(inputs),
            "execution_node_id": node_id,
            "profile_source_node_id": node.get('profile_source_node_id') or '02_CHUNK_EMBEDDING',
            "input_sha256s": [hashlib.sha256(value.encode("utf-8")).hexdigest().upper() for value in inputs],
        }
        retry_count = node.get("retry_count", 0)
        if isinstance(retry_count, bool) or not isinstance(retry_count, int) or not 0 <= retry_count <= 10:
            raise GatewayError(f"Core_NODE_RETRY_COUNT_INVALID:{node_id}")
        result: dict[str, Any] = {}
        for retry_index in range(retry_count + 1):
            sequence, _path = self._begin(
                capability="EMBEDDING",
                task_type=task_type,
                node_id=node_id,
                behavior={
                    **behavior_base,
                    "retry_index": retry_index,
                    "retry_limit": retry_count,
                },
            )
            try:
                with call_scope(job_id=self.evidence_root.parent.name, node_id=node_id, profile_ref=node.get("profile_ref"), task_type=task_type):
                    result = dict(
                        self.validation_runner.execute_embeddings(
                            profile_kind=node["profile_kind"],
                            service=execution_profile["service"],
                            model=execution_profile["model"],
                            inputs=inputs,
                            purpose=PURPOSE,
                            timeout_seconds=600,
                        )
                    )
            except ExecutionControlSignal as signal:
                self._finish(sequence, result={
                    'status': signal.state, 'reason': 'EXECUTION_'+signal.state,
                    'requested_model': execution_profile['model'].get('model_name'),
                    'execution_receipt': signal.execution_receipt,
                }, retry_index=retry_index, retry_limit=retry_count)
                raise
            except Exception as error:
                result = {
                    "status": "FAILED",
                    "reason": f"RUNNER_EXCEPTION:{type(error).__name__}:{error}",
                    "requested_model": execution_profile["model"].get("model_name"),
                    "returned_model": None,
                    "external_network_calls": 0,
                    "external_model_calls": 0,
                    "external_process_launches": 0,
                }
            self._finish(
                sequence,
                result=result,
                retry_index=retry_index,
                retry_limit=retry_count,
            )
            execution_checkpoint()
            if result.get("status") == "PASS" or not self._transient_retry_eligible(result):
                break
            if retry_index < retry_count:
                self._wait_before_retry(retry_index)
        vectors = result.get("vectors")
        if result.get("status") != "PASS" or not isinstance(vectors, list) or len(vectors) != len(inputs):
            raise GatewayError(f"Core_EMBED_FAILED:{result.get('reason')}")
        returned_model = str(result.get("returned_model") or "")
        canonical_model = returned_model.split("/")[-1].split(":")[0]
        return {
            "embeddings": vectors,
            "model": returned_model,
            "provider_model_id": returned_model,
            "embedding_model": canonical_model,
            "execution_receipt": dict(result.get("execution_receipt") or {}),
        }

    def healthcheck(self, task_type: str) -> dict[str, Any]:
        if task_type != "embed_local":
            raise InvalidHealthcheckTarget(f"Core_HEALTHCHECK_INVALID:{task_type}")
        node = self._node("02_CHUNK_EMBEDDING")
        profile = node["execution_profile"]
        service = profile["service"]
        ready = (
            node["profile_kind"] == "LOCAL"
            and service.get("endpoint_kind") == "ollama"
            and service.get("exact_identity_available") is True
            and service.get("execution_eligible") is True
        )
        return {
            "ready": ready,
            "model": profile["model"].get("model_name"),
            "reason": "FROZEN_LOCAL_PROFILE_READY" if ready else "FROZEN_LOCAL_PROFILE_NOT_READY",
        }

    def region_preflight(self, task_type: str, *, probe: Any = None) -> dict[str, Any]:
        del probe
        node_id = _TASK_NODE.get(task_type)
        if node_id is None:
            raise GatewayError(f"Core_REGION_TASK_NOT_MAPPED:{task_type}")
        node = self._node(node_id)
        kind = node["profile_kind"]
        provider = str(node.get("provider") or "")
        domestic = kind == "LOCAL" or "DEEPSEEK" in provider.upper() or "SILICON" in provider.upper()
        return {
            "ok": True,
            "gated": False,
            "reason": "domestic_skip" if domestic else "region_ok",
            "region": "LOCAL_OR_DOMESTIC_ROUTE" if domestic else "USER_CONFIGURED_SUBSCRIPTION_ROUTE",
            "provider": provider,
            "message": "Frozen Desktop node route is eligible for dispatch.",
        }

    def rerank(self, task_type: str, query: str, documents: list[str]) -> dict[str, Any]:
        del task_type, query, documents
        raise GatewayError("Core_RERANK_DISABLED_DISTANCE_FALLBACK")

    @property
    def isolate_native_pdf(self) -> bool:
        return True

    @property
    def page_ocr_ready(self) -> bool:
        node = self.nodes.get('01_DOCUMENT_INGEST')
        if not isinstance(node, Mapping) or node.get('enabled') is not True:
            return False
        if node.get('profile_ref') == 'PARSER_PROFILE':
            fallback = node.get('fallback_profile')
            if not isinstance(fallback, Mapping):
                return False
        else:
            try:
                self._node('01_DOCUMENT_INGEST')
            except GatewayError:
                return False
        return callable(getattr(self.validation_runner, 'execute_ocr_image', None))

    def ocr(
        self,
        task_type: str,
        image_bytes: bytes,
        *,
        mime_type: str,
        page_number: int,
        source_sha256: str,
        data_ownership: str,
    ) -> dict[str, Any]:
        if task_type != 'ocr' or data_ownership != 'self':
            raise OcrResultInvalid('Core_PAGE_OCR_OWNERSHIP_OR_TASK_INVALID')
        if (not isinstance(image_bytes, bytes) or not image_bytes or
            isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1 or
            not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256.upper())):
            raise OcrResultInvalid('Core_PAGE_OCR_SOURCE_BINDING_INVALID')
        from document_processing.ocr_page_rescue import _ocr_text_error
        node_id = '01_DOCUMENT_INGEST'
        configured = self.nodes.get(node_id)
        if not isinstance(configured, Mapping) or configured.get('enabled') is not True:
            raise OcrResultInvalid('Core_PAGE_OCR_NODE_MISSING')
        fallback = configured.get('fallback_profile')
        if configured.get('fallback_profile_ref') and not isinstance(fallback, Mapping):
            raise OcrResultInvalid('Core_PAGE_OCR_FALLBACK_BINDING_MISSING')
        if fallback is not None and (
            not isinstance(fallback, Mapping)
            or fallback.get('profile_ref') == configured.get('profile_ref')
            or fallback.get('profile_ref') != configured.get('fallback_profile_ref')
            or fallback.get('profile_kind') not in {'API', 'CLI', 'LOCAL'}
            or not isinstance(fallback.get('execution_profile'), Mapping)
            or set(fallback['execution_profile']) != {'service', 'model'}
            or not isinstance(fallback['execution_profile'].get('model'), Mapping)
            or not fallback['execution_profile']['model'].get('model_name')
        ):
            raise OcrResultInvalid('Core_PAGE_OCR_FALLBACK_BINDING_INVALID')
        if configured.get('profile_ref') == 'PARSER_PROFILE':
            if fallback is None:
                raise OcrResultInvalid('Core_PAGE_OCR_FALLBACK_REQUIRED_FOR_LOCAL_PARSER')
            primary = {**dict(fallback), 'retry_count': configured.get('retry_count', 0)}
            fallback = None
        else:
            primary = self._node(node_id)
        prompt_path = Path(__file__).resolve().parents[1] / 'model_gateway/prompts/ocr/v6.md'
        prompt = prompt_path.read_text(encoding='utf-8')
        page_binding = {'source_sha256': source_sha256.upper(), 'page_number': page_number,
            'image_sha256': hashlib.sha256(image_bytes).hexdigest().upper(),
            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest().upper(),
            'mime_type': mime_type}
        routes = {'PRIMARY': primary, **({'FALLBACK': dict(fallback)} if fallback is not None else {})}
        # A completed page is reusable only under its original exact model binding.
        for route in routes.values():
            binding = {**page_binding, 'profile_sha256': _sha256(route['execution_profile'])}
            key = _sha256(binding)
            checkpoint = self.evidence_root / 'ocr_pages' / (key + '.json')
            execution_checkpoint()
            if not checkpoint.is_file():
                continue
            cached = json.loads(checkpoint.read_text(encoding='utf-8'))
            response = cached.get('response', {})
            if (cached.get('binding') != binding or _ocr_text_error(response.get('text'))
                    or cached.get('response_payload_sha256') != _sha256(response)
                    or not response.get('request_id')):
                raise OcrResultInvalid('Core_PAGE_OCR_CHECKPOINT_INVALID')
            _atomic_json(self.evidence_root / 'ocr_replays' / (uuid.uuid4().hex + '.json'),
                {'cache_key': key, 'binding': binding,
                 'original_execution_id': response['request_id'], 'new_provider_call': False})
            return response
        retries = primary.get('retry_count', 0)
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10:
            raise OcrResultInvalid('Core_NODE_RETRY_COUNT_INVALID:01_DOCUMENT_INGEST')
        route_bindings = {role: {key: route.get(key) for key in
            ('profile_ref', 'profile_kind', 'execution_profile')} for role, route in routes.items()}
        state_binding = {**page_binding, 'routes_sha256': _sha256(route_bindings)}
        state_path = self.evidence_root / 'ocr_retry_state' / (_sha256(state_binding) + '.json')
        state = {'binding': state_binding, 'attempt': 0, 'role': 'PRIMARY', 'status': 'NEW', 'sequence': None}
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if (state.get('binding') != state_binding or state.get('role') not in routes
                    or type(state.get('attempt')) is not int or not 0 <= state['attempt'] <= 10
                    or state.get('status') not in {'IN_FLIGHT', 'FAILED', 'STOPPED', 'PAUSED', 'CANCELLED', 'PASS'}):
                raise OcrResultInvalid('Core_PAGE_OCR_RETRY_STATE_INVALID')
            # Persisted call receipts win if shutdown interrupted the state update.
            seq = state.get('sequence')
            post_path = self.evidence_root / f'call-{seq:06d}.post.json' if type(seq) is int else None
            if state['status'] == 'IN_FLIGHT' and post_path and post_path.is_file():
                post = json.loads(post_path.read_text(encoding='utf-8'))
                state['status'] = post.get('status', 'IN_FLIGHT')
                state['reason'] = post.get('reason')
                state['execution_receipt'] = post.get('execution_receipt')
            if state['status'] == 'PASS':
                # Never silently call again after losing a success checkpoint.
                raise OcrResultInvalid('Core_PAGE_OCR_SUCCESS_CHECKPOINT_MISSING')
        def retryable(result):
            reason = str(result.get('reason') or '')
            quality = reason.startswith('OCR_TEXT_INVALID:') or reason == 'OCR_RESPONSE_TRUNCATED'
            return quality or self._transient_retry_eligible(result)
        def fallback_eligible(result):
            reason = str(result.get('reason') or '')
            return retryable(result) or reason in {
                'OCR_HTTP_400', 'OCR_HTTP_401', 'OCR_HTTP_403', 'OCR_HTTP_404', 'OCR_HTTP_422',
                'OCR_IMAGE_NOT_SUPPORTED', 'OCR_CAPABILITY_NOT_SUPPORTED'}
        attempt = state['attempt']
        role = state['role']
        if state['status'] in {'FAILED', 'IN_FLIGHT'}:
            previous = {'status': 'FAILED', 'reason': state.get('reason'),
                        'execution_receipt': state.get('execution_receipt')}
            # An interrupted unknown dispatch is retained and consumes the next
            # attempt. Pause/cancel signals themselves never activate fallback.
            if state['status'] != 'IN_FLIGHT' and not retryable(previous) and not (
                role == 'PRIMARY' and 'FALLBACK' in routes and fallback_eligible(previous)):
                raise OcrResultInvalid(f"Core_PAGE_OCR_FAILED:{state.get('reason')}")
            attempt += 1
            if role == 'PRIMARY' and 'FALLBACK' in routes and fallback_eligible(previous):
                role = 'FALLBACK'
        if attempt > retries:
            raise OcrResultInvalid('Core_PAGE_OCR_RETRIES_EXHAUSTED')
        while attempt <= retries:
            node = routes[role]
            profile = node['execution_profile']
            context = {'scope': 'PAGE', 'page_number': page_number, 'model_role': role,
                       'source_sha256': source_sha256.upper()}
            sequence, _ = self._begin(capability='OCR', task_type=task_type, node_id=node_id,
                effective_node=node, behavior={**page_binding, 'retry_index': attempt,
                    'retry_limit': retries, 'model_role': role})
            state = {'binding': state_binding, 'attempt': attempt, 'role': role,
                     'status': 'IN_FLIGHT', 'sequence': sequence}
            _atomic_json(state_path, state)
            try:
                with call_scope(job_id=self.evidence_root.parent.name, node_id=node_id,
                                profile_ref=node.get('profile_ref'), task_type=task_type):
                    result = dict(self.validation_runner.execute_ocr_image(
                        profile_kind=node['profile_kind'], service=profile['service'], model=profile['model'],
                        image_bytes=image_bytes, mime_type=mime_type, prompt=prompt,
                        purpose=PURPOSE, max_output_tokens=None, timeout_seconds=600))
            except ExecutionControlSignal as signal:
                self._finish(sequence, result={'status': signal.state, 'reason': 'EXECUTION_'+signal.state,
                    'requested_model': profile['model']['model_name'], 'execution_receipt': signal.execution_receipt},
                    retry_index=attempt, retry_limit=retries, retry_context=context)
                _atomic_json(state_path, {**state, 'status': signal.state})
                raise
            except Exception as error:
                result = {'status': 'FAILED', 'reason': f'RUNNER_EXCEPTION:{type(error).__name__}',
                          'requested_model': profile['model']['model_name']}
            if result.get('status') == 'PASS':
                if result.get('finish_reason') in {'length', 'max_tokens'}:
                    result.update(status='FAILED', reason='OCR_RESPONSE_TRUNCATED')
                else:
                    text_error = _ocr_text_error(result.get('text'))
                    if text_error:
                        result.update(status='FAILED', reason='OCR_TEXT_INVALID:'+text_error)
            receipt = result.get('execution_receipt') or {}
            if result.get('status') == 'PASS' and (not receipt.get('response_sha256') or not receipt.get('execution_id')):
                result.update(status='FAILED', reason='OCR_EXECUTION_EVIDENCE_MISSING')
            self._finish(sequence, result=result, retry_index=attempt, retry_limit=retries, retry_context=context)
            state.update(status=result.get('status'), reason=result.get('reason'), execution_receipt=receipt)
            if result.get('status') == 'PASS':
                response = {'text': result['text'], 'model': result.get('returned_model'),
                    'response_sha256': receipt['response_sha256'], 'request_id': receipt['execution_id'],
                    'execution_receipt': receipt}
                binding = {**page_binding, 'profile_sha256': _sha256(profile)}
                _atomic_json(self.evidence_root / 'ocr_pages' / (_sha256(binding)+'.json'),
                    {'binding': binding, 'response': response, 'response_payload_sha256': _sha256(response)})
                _atomic_json(state_path, state)
                return response
            _atomic_json(state_path, state)
            use_fallback = role == 'PRIMARY' and 'FALLBACK' in routes and fallback_eligible(result)
            if attempt >= retries or not (use_fallback or retryable(result)):
                raise OcrResultInvalid(f"Core_PAGE_OCR_FAILED:{result.get('reason')}")
            self._wait_before_retry(attempt)
            attempt += 1
            if use_fallback:
                role = 'FALLBACK'
        raise OcrResultInvalid('Core_PAGE_OCR_RETRIES_EXHAUSTED')

    def metrics(self) -> dict[str, Any]:
        value = dict(self._totals)
        value["estimated_cost_cny"] = round(float(value["estimated_cost_cny"]), 12)
        value["evidence_root"] = str(self.evidence_root)
        return value


__all__ = ["PURPOSE", "CoreModelBridge"]
