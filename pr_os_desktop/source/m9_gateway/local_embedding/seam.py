"""P05/T11 provider-neutral local Embedding seam.

This module adds only the T11 compatibility and thin-adapter surface. Process
isolation, routing, normalization, handoff ordering, and attempt lineage remain
owned by the accepted T02 ExecutionCore. M11 remains the only ledger writer;
this module returns the exact binding consumed by T03 ChannelAttemptBuilder.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from m9_gateway.execution_core.contracts import (
    base_object,
    canonical_json_bytes,
    canonical_sha256,
    immutable_copy,
    object_ref,
    utc_now,
    validate_t01_object,
)
from m9_gateway.execution_core.core import ExecutionCore, ExecutionOutcome
from m9_gateway.execution_core.handoff import HandoffContract
from m9_gateway.execution_core.registry import AdapterRegistry
from m9_gateway.execution_core.routing import ChannelSelector
from m9_gateway.execution_core.runner import ProcessSpec


COMPATIBILITY_SCHEMA_VERSION = "P05_T11_LOCAL_SEAM_COMPATIBILITY_V1"
WORKER_REQUEST_VERSION = "P05_T11_LOCAL_EMBEDDING_WORKER_REQUEST_V1"
WORKER_RESULT_VERSION = "P05_T11_LOCAL_EMBEDDING_WORKER_RESULT_V1"
WORKER_VERSION = "P05_T11_LOCAL_EMBEDDING_WORKER_V1"
OUTPUT_SCHEMA_REF = "schema:p05-t11-local-embedding-worker-result@v1"
ADAPTER_ID = "p05_t11_local_embedding"
PROFILE_ID = "p05_t11_local_embedding_shadow"
LOGICAL_ROLE = "local_embedding"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
COMPATIBILITY_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "p05_t11_local_seam_compatibility_v1.schema.json"
)

WORKER_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "worker_version",
        "status",
        "request_started",
        "requested_model",
        "returned_model",
        "model_manifest_sha256",
        "embeddings",
        "vector_count",
        "dimension",
        "normalization",
        "input_hash",
        "output_hash",
        "latency_ms",
        "error_code",
    ],
    "properties": {
        "schema_version": {"const": WORKER_RESULT_VERSION},
        "worker_version": {"const": WORKER_VERSION},
        "status": {"enum": ["ok", "error"]},
        "request_started": {"type": "boolean"},
        "requested_model": {"type": ["string", "null"]},
        "returned_model": {"type": ["string", "null"]},
        "model_manifest_sha256": {"type": ["string", "null"]},
        "embeddings": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        "vector_count": {"type": "integer", "minimum": 0},
        "dimension": {"type": ["integer", "null"], "minimum": 1},
        "normalization": {"type": ["string", "null"]},
        "input_hash": {"type": ["string", "null"]},
        "output_hash": {"type": ["string", "null"]},
        "latency_ms": {"type": "number", "minimum": 0},
        "error_code": {"type": ["string", "null"]},
    },
}


class LocalSeamViolation(ValueError):
    def __init__(self, code: str, details: Sequence[str] = ()):
        self.code = code
        self.details = tuple(details)
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def _canonical_model(value: str) -> str:
    return value.rsplit("/", 1)[-1].split(":", 1)[0]


def _require_loopback_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise LocalSeamViolation("LOCAL_ENDPOINT_INVALID") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOOPBACK_HOSTS
        or port != 11434
        or parsed.path != "/api/embed"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalSeamViolation("LOCAL_ENDPOINT_NOT_EXACT_LOOPBACK")


def _vector_key_seed(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runtime": {
            "provider": value["runtime"]["provider"],
            "version": value["runtime"]["version"],
            "executable_sha256": value["runtime"]["executable_sha256"],
            "endpoint": value["runtime"]["endpoint"],
        },
        "model": immutable_copy(value["model"]),
        "chroma": {
            "client_mode": value["chroma"]["client_mode"],
            "version": value["chroma"]["version"],
            "distance_metric": value["chroma"]["distance_metric"],
        },
    }


def _manifest_identity_seed(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: immutable_copy(item)
        for key, item in value.items()
        if key not in {"created_at", "manifest_identity_sha256"}
    }


def validate_compatibility_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = immutable_copy(value)
    schema = json.loads(COMPATIBILITY_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(accepted),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        details = [
            f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:20]
        ]
        raise LocalSeamViolation("COMPATIBILITY_MANIFEST_SCHEMA_INVALID", details)
    _require_loopback_endpoint(accepted["runtime"]["endpoint"])
    if _canonical_model(accepted["model"]["requested_model"]) != accepted["model"]["canonical_model"]:
        raise LocalSeamViolation("COMPATIBILITY_MODEL_CANONICAL_MISMATCH")
    limits = accepted["limits"]
    if not limits["normalization_min"] <= 1.0 <= limits["normalization_max"]:
        raise LocalSeamViolation("COMPATIBILITY_NORMALIZATION_RANGE_INVALID")
    generation_root = Path(accepted["chroma"]["generation_root"])
    if (
        not generation_root.is_absolute()
        or any(ord(character) > 127 for character in str(generation_root))
        or generation_root.name in {"chroma-sandbox-v2", "chroma-vault"}
    ):
        raise LocalSeamViolation("COMPATIBILITY_CHROMA_PATH_INVALID")
    vector_key = canonical_sha256(_vector_key_seed(accepted))
    if accepted["vector_compatibility_key_sha256"] != vector_key:
        raise LocalSeamViolation("VECTOR_COMPATIBILITY_KEY_MISMATCH")
    identity = canonical_sha256(_manifest_identity_seed(accepted))
    if accepted["manifest_identity_sha256"] != identity:
        raise LocalSeamViolation("COMPATIBILITY_MANIFEST_IDENTITY_MISMATCH")
    return accepted


def build_compatibility_manifest(
    *,
    manifest_id: str,
    runtime_version: str,
    executable_sha256: str,
    endpoint: str,
    requested_model: str,
    canonical_model: str,
    model_manifest_sha256: str,
    model_layer_digest: str,
    dimension: int,
    chroma_version: str,
    generation_root: str | Path,
    reranker_mode: str,
    source_evidence_refs: Sequence[str],
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(manifest_id):
        raise LocalSeamViolation("COMPATIBILITY_MANIFEST_ID_INVALID")
    value: dict[str, Any] = {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "created_at": clock(),
        "source_evidence_refs": list(source_evidence_refs),
        "runtime": {
            "provider": "ollama",
            "version": runtime_version,
            "executable_sha256": executable_sha256.upper(),
            "process_transport": "local_process",
            "endpoint": endpoint,
            "network_policy": "LOOPBACK_ONLY",
            "daemon_policy": "USE_EXISTING_ONLY_NO_START_STOP_RESTART",
        },
        "model": {
            "requested_model": requested_model,
            "canonical_model": canonical_model,
            "manifest_sha256": model_manifest_sha256.upper(),
            "model_layer_digest": model_layer_digest.upper(),
            "dimension": dimension,
            "normalization": "L2_UNIT",
            "input_dtype": "UTF8_TEXT",
            "output_dtype": "JSON_NUMBER_FINITE",
        },
        "limits": {
            "max_batch_size": 4,
            "max_input_utf8_bytes": 16384,
            "timeout_seconds": 60,
            "normalization_min": 0.999,
            "normalization_max": 1.001,
        },
        "physical_start_envelope": {
            "construction_a_embedding_requests_max": 4,
            "shadow_b_embedding_requests_exact": 1,
            "loopback_health_requests_max": 2,
            "automatic_retry_per_request": 0,
            "total_loopback_requests_max": 7,
        },
        "chroma": {
            "client_mode": "PERSISTENT_CLIENT_LOCAL",
            "version": chroma_version,
            "distance_metric": "L2",
            "path_policy": "ASCII_RUN_SUBTREE",
            "generation_root": str(Path(generation_root).resolve(strict=False)),
        },
        "reranker": {
            "mode": reranker_mode,
            "real_model_calls_max": 0,
            "cloud_fallback": False,
        },
        "accounting": {
            "inference_location": "local_machine",
            "access_mode": "local_runtime",
            "billing_mode": "local_compute",
            "provider_tokens": "N/A_LOCAL_COMPUTE",
            "api_cost_cny": 0,
            "external_request_limit": 0,
        },
    }
    value["vector_compatibility_key_sha256"] = canonical_sha256(_vector_key_seed(value))
    value["manifest_identity_sha256"] = canonical_sha256(_manifest_identity_seed(value))
    return validate_compatibility_manifest(value)


def _environment_names() -> tuple[str, ...]:
    names = [
        "PROS_P05_T11_PRESTART_PATH",
        "PROS_P05_T11_TERMINAL_PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING",
    ]
    if os.name == "nt":
        names.extend(["SYSTEMROOT", "TEMP", "TMP"])
    return tuple(sorted(names))


@dataclass(frozen=True)
class LocalEmbeddingOutcome:
    success: bool
    error_code: str | None
    embeddings: tuple[tuple[float, ...], ...]
    request_started: bool
    core_outcome: ExecutionOutcome
    model_receipt: dict[str, Any] | None
    handoff: dict[str, Any]
    channel_binding: dict[str, Any]
    compatibility_manifest: dict[str, Any]


class LocalEmbeddingSeam:
    """Thin local-model adapter that composes, but does not replace, T02."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        worker_script: str | Path | None = None,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable_copy(core_schema)
        self._clock = clock
        self._worker_script = Path(
            worker_script or Path(__file__).resolve().with_name("worker.py")
        ).resolve(strict=True)
        if not self._worker_script.is_file() or self._worker_script.is_symlink():
            raise LocalSeamViolation("LOCAL_WORKER_PATH_INVALID")
        self._registry = AdapterRegistry(self._core_schema)
        self._adapter_manifest = self._build_adapter_manifest()
        record = self._registry.register(self._adapter_manifest, enabled=True)
        self._adapter_manifest_hash = record.manifest_sha256
        selector = ChannelSelector(self._core_schema, self._registry, clock=clock)
        self._core = ExecutionCore(self._core_schema, selector)
        self._handoff = HandoffContract(self._core_schema, clock=clock)

    def _build_adapter_manifest(self) -> dict[str, Any]:
        value = base_object(
            "AdapterManifest",
            "adaptermanifest_p05_t11_local_embedding",
            producer="P05_T11_LOCAL_SEAM",
            single_writer="P05_T11_LOCAL_SEAM",
            consumers=("P05_ROUTER", "P05_JOB_RUNNER", "P05_T03_M11"),
            clock=self._clock,
            source_evidence_refs=("P05_T11_OPERATION_MANUAL@r0.1",),
            extensions={"read_roots": [str(self._worker_script)]},
        )
        value.update(
            {
                "adapter_id": ADAPTER_ID,
                "adapter_type": "local_model_loopback_worker",
                "executable_name": str(Path(sys.executable).resolve()),
                "install_discovery": "H0_STATIC_EXACT_IDENTITY_ONLY",
                "version_discovery": "H0_STATIC_REGISTRY_AND_EXECUTABLE_SHA256",
                "auth_discovery": None,
                "session_discovery": None,
                "model_discovery": "FROZEN_MANIFEST_ONLY_NO_DYNAMIC_PULL",
                "capability_discovery": "FROZEN_T11_COMPATIBILITY_MANIFEST",
                "permission_flags": ["NO_SHELL", "NO_DOWNLOAD", "NO_FALLBACK"],
                "workspace_flags": ["ISOLATED_CREATE_ONLY"],
                "network_flags": ["LOOPBACK_ONLY", "PROXY_DISABLED"],
                "output_modes": ["STRICT_JSON_STDOUT", "CREATE_ONLY_SIDECARS"],
                "exit_code_mapping": [{"exit_code": 0, "state": "PARSE_SEMANTIC_RESULT"}],
                "timeout_kill_policy": "T02_KILL_PROCESS_TREE",
                "redaction_policy": "ARGV_VALUES_AND_ENV_VALUES_OMITTED_PAYLOAD_HASH_ONLY",
                "version_range": WORKER_VERSION,
                "platforms": [sys.platform],
            }
        )
        return validate_t01_object(value, "AdapterManifest", self._core_schema)

    def _profile(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        value = base_object(
            "ExecutionProfile",
            "executionprofile_p05_t11_local_embedding_shadow",
            producer="P05_T11_LOCAL_SEAM",
            single_writer="P05_T11_LOCAL_SEAM",
            consumers=("P05_ROUTER", "P05_JOB_RUNNER", "P05_EVIDENCE_ADAPTER"),
            clock=self._clock,
            source_evidence_refs=(manifest["manifest_id"],),
            extensions={
                "adapter_manifest_revision": "candidate001",
                "qualification_state": "Q2",
                "minimum_route_qualification": "Q2",
                "capabilities": ["embedding_text", "json_output", "loopback_only"],
                "compatibility_manifest_identity": manifest["manifest_identity_sha256"],
                "production_routable": False,
            },
        )
        value.update(
            {
                "profile_id": PROFILE_ID,
                "status": "SHADOW",
                "adapter_id": ADAPTER_ID,
                "adapter_manifest_hash": self._adapter_manifest_hash,
                "channel_axes": {
                    "process_transport": "local_process",
                    "inference_location": "local_machine",
                    "access_mode": "local_runtime",
                    "billing_mode": "local_compute",
                },
                "evidence_class": "local_compute",
                "provider": "ollama",
                "requested_model": manifest["model"]["requested_model"],
                "credential_ref": None,
                "route": "loopback_http",
                "region": "local_machine",
                "egress": "loopback_only",
                "permission_policy": {"shell": False, "tools": []},
                "workspace_policy": {
                    "mode": "isolated_create_only",
                    "environment_allowlist": list(_environment_names()),
                },
                "network_policy": {
                    "allow": [],
                    "local_loopback_enforced_by_worker": True,
                    "external_egress": False,
                },
                "output_contract": {
                    "mode": "strict_json_stdout",
                    "schema_ref": OUTPUT_SCHEMA_REF,
                    "schema_sha256": canonical_sha256(WORKER_RESULT_SCHEMA),
                    "raw_cap_bytes": 524288,
                },
                "retry_policy": {"max_attempts": 1, "automatic_retry": False},
                "qualification_report_ref": None,
            }
        )
        return validate_t01_object(value, "ExecutionProfile", self._core_schema)

    def _role_binding(self) -> dict[str, Any]:
        value = base_object(
            "LogicalRoleBinding",
            "logicalrolebinding_p05_t11_local_embedding",
            producer="P05_T11_LOCAL_SEAM",
            single_writer="P05_T11_LOCAL_SEAM",
            consumers=("P05_ROUTER", "M14_QUALIFICATION_RUNNER"),
            clock=self._clock,
            source_evidence_refs=("P05_T11_OPERATION_MANUAL@r0.1",),
        )
        value.update(
            {
                "role_id": LOGICAL_ROLE,
                "input_schema_ref": "schema:p05-t11-local-embedding-worker-request@v1",
                "output_schema_ref": OUTPUT_SCHEMA_REF,
                "acceptable_evidence_classes": ["local_compute"],
                "minimum_model_identity_grade": "EXACT_DIGEST_AND_RETURNED_MODEL",
                "required_capabilities": ["embedding_text", "json_output", "loopback_only"],
                "data_rules": {"allowed_classifications": ["public_safe_synthetic"]},
                "timeout_seconds": 60,
                "attempt_limit": 1,
                "retry_classes": [],
                "candidate_profile_allowlist": [PROFILE_ID],
                "fallback_policy": {
                    "mode": "FAIL_CLOSED_NO_CLOUD_FALLBACK",
                    "new_attempt_required_after_process_start": True,
                },
            }
        )
        return validate_t01_object(value, "LogicalRoleBinding", self._core_schema)

    def _request(
        self,
        request_bytes: bytes,
        *,
        job_id: str,
        attempt_no: int,
        source_evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        value = base_object(
            "ExecutionRequest",
            f"executionrequest_{job_id}_{attempt_no}",
            producer="P05_T11_LOCAL_SEAM",
            single_writer="P05_T11_LOCAL_SEAM",
            consumers=("P05_ROUTER", "P05_JOB_RUNNER"),
            clock=self._clock,
            source_evidence_refs=source_evidence_refs,
        )
        value.update(
            {
                "logical_operation_id": f"local_embedding_{job_id}",
                "attempt_no": attempt_no,
                "logical_role": LOGICAL_ROLE,
                "purpose_code": "P05_T11_LOCAL_EMBEDDING_COMPATIBILITY",
                "caller_module": "P05_T11_LOCAL_SEAM",
                "input_refs": ["job_input:request.json"],
                "input_hashes": [
                    {
                        "relative_path": "request.json",
                        "bytes": len(request_bytes),
                        "sha256": __import__("hashlib").sha256(request_bytes).hexdigest().upper(),
                    }
                ],
                "output_schema_ref": OUTPUT_SCHEMA_REF,
                "data_classification": "public_safe_synthetic",
                "allowed_egress_class": "loopback_only",
                "route_policy_ref": "P05_T11_LOOPBACK_ONLY_NO_FALLBACK@candidate001",
                "working_directory_policy": "isolated_create_only",
                "read_roots": [],
                "write_roots": [],
                "tool_policy": {"shell": False, "tools": []},
                "timeout_seconds": 60,
                "idempotency_key": f"{job_id}:{attempt_no}",
                "requester": "P05_T11_LOCAL_SEAM",
            }
        )
        return validate_t01_object(value, "ExecutionRequest", self._core_schema)

    @staticmethod
    def _validate_vectors(payload: Mapping[str, Any], manifest: Mapping[str, Any], expected_count: int) -> tuple[tuple[float, ...], ...]:
        if payload.get("status") != "ok" or payload.get("error_code") is not None:
            raise LocalSeamViolation("LOCAL_EMBEDDING_WORKER_REPORTED_ERROR")
        if payload.get("requested_model") != manifest["model"]["requested_model"]:
            raise LocalSeamViolation("LOCAL_EMBEDDING_REQUESTED_MODEL_MISMATCH")
        returned = payload.get("returned_model")
        if (
            not isinstance(returned, str)
            or returned != manifest["model"]["requested_model"]
            or _canonical_model(returned) != manifest["model"]["canonical_model"]
        ):
            raise LocalSeamViolation("LOCAL_EMBEDDING_RETURNED_MODEL_MISMATCH")
        if payload.get("model_manifest_sha256") != manifest["model"]["manifest_sha256"]:
            raise LocalSeamViolation("LOCAL_EMBEDDING_MANIFEST_DIGEST_MISMATCH")
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != expected_count:
            raise LocalSeamViolation("LOCAL_EMBEDDING_VECTOR_COUNT_MISMATCH")
        result: list[tuple[float, ...]] = []
        dimension = manifest["model"]["dimension"]
        lower = manifest["limits"]["normalization_min"]
        upper = manifest["limits"]["normalization_max"]
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != dimension:
                raise LocalSeamViolation("LOCAL_EMBEDDING_VECTOR_DIMENSION_MISMATCH")
            if any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                for item in vector
            ):
                raise LocalSeamViolation("LOCAL_EMBEDDING_VECTOR_NONFINITE")
            normalized = tuple(float(item) for item in vector)
            norm = math.sqrt(sum(item * item for item in normalized))
            if not lower <= norm <= upper:
                raise LocalSeamViolation("LOCAL_EMBEDDING_VECTOR_NORMALIZATION_MISMATCH")
            result.append(normalized)
        return tuple(result)

    @staticmethod
    def _read_json_if_present(path: Path) -> dict[str, Any] | None:
        if not path.is_file() or path.is_symlink():
            return None
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalSeamViolation("LOCAL_MODEL_SIDECAR_INVALID") from exc
        if not isinstance(decoded, dict):
            raise LocalSeamViolation("LOCAL_MODEL_SIDECAR_NOT_OBJECT")
        return decoded

    @staticmethod
    def _validate_sidecars(
        prestart: Mapping[str, Any] | None,
        terminal: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
        manifest: Mapping[str, Any],
        *,
        behavior_hash: str,
        input_hash: str,
        request_body_sha256: str,
    ) -> None:
        if prestart is None or terminal is None:
            raise LocalSeamViolation("LOCAL_MODEL_SIDECAR_INCOMPLETE")
        prestart_expected = {
            "schema_version": "P05_T11_LOCAL_MODEL_PRESTART_V1",
            "request_started": True,
            "route": "loopback_http",
            "requested_model": manifest["model"]["requested_model"],
            "model_manifest_sha256": manifest["model"]["manifest_sha256"],
            "behavior_hash": behavior_hash,
            "input_hash": input_hash,
            "request_body_sha256": request_body_sha256,
        }
        terminal_expected = {
            "schema_version": "P05_T11_LOCAL_MODEL_TERMINAL_V1",
            "status": "ok",
            "request_started": True,
            "requested_model": manifest["model"]["requested_model"],
            "returned_model": payload.get("returned_model"),
            "model_manifest_sha256": manifest["model"]["manifest_sha256"],
            "behavior_hash": behavior_hash,
            "input_hash": input_hash,
            "output_hash": payload.get("output_hash"),
            "dimension": manifest["model"]["dimension"],
            "normalization": manifest["model"]["normalization"],
            "error_code": None,
        }
        payload_expected = {
            "status": "ok",
            "request_started": True,
            "vector_count": len(payload.get("embeddings") or []),
            "dimension": manifest["model"]["dimension"],
            "normalization": manifest["model"]["normalization"],
            "input_hash": input_hash,
            "output_hash": canonical_sha256(payload.get("embeddings")),
            "error_code": None,
        }
        if any(payload.get(key) != expected for key, expected in payload_expected.items()):
            raise LocalSeamViolation("LOCAL_MODEL_PAYLOAD_BINDING_MISMATCH")
        if any(prestart.get(key) != expected for key, expected in prestart_expected.items()):
            raise LocalSeamViolation("LOCAL_MODEL_PRESTART_BINDING_MISMATCH")
        if prestart.get("endpoint_host") not in LOOPBACK_HOSTS:
            raise LocalSeamViolation("LOCAL_MODEL_PRESTART_ROUTE_MISMATCH")
        if any(terminal.get(key) != expected for key, expected in terminal_expected.items()):
            raise LocalSeamViolation("LOCAL_MODEL_TERMINAL_BINDING_MISMATCH")

    def _model_receipt(
        self,
        process_receipt: Mapping[str, Any],
        manifest: Mapping[str, Any],
        terminal_sidecar: Mapping[str, Any] | None,
        *,
        behavior_hash: str,
        input_hash: str,
        output_hash: str,
        request_started: bool,
        success: bool,
    ) -> dict[str, Any]:
        value = base_object(
            "ModelExecutionReceipt",
            f"modelexecutionreceipt_{process_receipt['job_id']}_{process_receipt['attempt_id']}",
            producer="P05_T11_LOCAL_SEAM",
            single_writer="P05_T11_LOCAL_SEAM",
            consumers=("P05_T03_M11", "M14", "M13"),
            clock=self._clock,
            source_evidence_refs=(process_receipt["object_id"], manifest["manifest_id"]),
            extensions={
                "runtime_version": manifest["runtime"]["version"],
                "runtime_executable_sha256": manifest["runtime"]["executable_sha256"],
                "model_manifest_sha256": manifest["model"]["manifest_sha256"],
                "model_layer_digest": manifest["model"]["model_layer_digest"],
                "dimension": manifest["model"]["dimension"],
                "normalization": manifest["model"]["normalization"],
                "provider_tokens_are_not_available": True,
                "request_started_from_create_only_sidecar": request_started,
            },
        )
        returned = terminal_sidecar.get("returned_model") if terminal_sidecar else None
        latency = (
            terminal_sidecar.get("latency_ms")
            if terminal_sidecar and isinstance(terminal_sidecar.get("latency_ms"), (int, float))
            else process_receipt.get("duration_ms")
        )
        value.update(
            {
                "job_id": process_receipt["job_id"],
                "attempt_id": process_receipt["attempt_id"],
                "evidence_class": "local_compute",
                "requested_model": manifest["model"]["requested_model"],
                "returned_model": returned,
                "model_identity_grade": "EXACT_DIGEST_AND_RETURNED_MODEL" if success else "UNKNOWN",
                "provider": "ollama",
                "route": "loopback_http",
                "region": "local_machine",
                "egress": "loopback_only",
                "behavior_hash": behavior_hash,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "usage_status": "N/A_LOCAL_COMPUTE",
                "token_usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                    "total_tokens": None,
                },
                "cost_status": "N/A_LOCAL_COMPUTE",
                "cost_value": None,
                "currency": None,
                "latency_ms": float(latency or 0),
                "finish_status": "COMPLETED" if success else "ERROR",
                "external_request_started": False,
            }
        )
        return validate_t01_object(value, "ModelExecutionReceipt", self._core_schema)

    def execute(
        self,
        texts: Sequence[str],
        compatibility_manifest: Mapping[str, Any],
        *,
        workspace_parent: str | Path,
        protected_roots: Sequence[str | Path],
        job_id: str,
        attempt_id: str,
        attempt_no: int = 1,
        retry_of_attempt_id: str | None = None,
        stage: str = "CONSTRUCTION_A",
        data_ownership: str,
        source_evidence_refs: Sequence[str] = ("P05_T11_LOCAL_START_AUTHORIZATION@run001",),
    ) -> LocalEmbeddingOutcome:
        manifest = validate_compatibility_manifest(compatibility_manifest)
        if not SAFE_ID.fullmatch(job_id) or not SAFE_ID.fullmatch(attempt_id):
            raise LocalSeamViolation("LOCAL_EMBEDDING_ATTEMPT_ID_INVALID")
        if attempt_no != 1 or retry_of_attempt_id is not None:
            raise LocalSeamViolation("LOCAL_EMBEDDING_RETRY_NOT_AUTHORIZED")
        if data_ownership not in {"self", "entrusted"}:
            raise LocalSeamViolation("LOCAL_EMBEDDING_OWNERSHIP_INVALID")
        text_list = list(texts)
        if (
            not text_list
            or len(text_list) > manifest["limits"]["max_batch_size"]
            or any(not isinstance(item, str) or not item for item in text_list)
        ):
            raise LocalSeamViolation("LOCAL_EMBEDDING_FIXTURE_INVALID")
        input_hash = canonical_sha256(text_list)
        behavior_hash = canonical_sha256(
            {
                "worker_version": WORKER_VERSION,
                "compatibility_manifest_identity": manifest["manifest_identity_sha256"],
                "output_schema_sha256": canonical_sha256(WORKER_RESULT_SCHEMA),
                "data_ownership": data_ownership,
                "retry": 0,
                "fallback": "FORBIDDEN",
            }
        )
        worker_request = {
            "schema_version": WORKER_REQUEST_VERSION,
            "endpoint": manifest["runtime"]["endpoint"],
            "model": manifest["model"]["requested_model"],
            "model_manifest_sha256": manifest["model"]["manifest_sha256"],
            "expected_canonical_model": manifest["model"]["canonical_model"],
            "expected_dimension": manifest["model"]["dimension"],
            "normalization": manifest["model"]["normalization"],
            "normalization_min": manifest["limits"]["normalization_min"],
            "normalization_max": manifest["limits"]["normalization_max"],
            "max_batch_size": manifest["limits"]["max_batch_size"],
            "max_input_utf8_bytes": manifest["limits"]["max_input_utf8_bytes"],
            "timeout_seconds": manifest["limits"]["timeout_seconds"],
            "behavior_hash": behavior_hash,
            "texts": text_list,
        }
        request_bytes = canonical_json_bytes(worker_request)
        request_body_sha256 = canonical_sha256(
            {"model": manifest["model"]["requested_model"], "input": text_list}
        )
        profile = self._profile(manifest)
        role_binding = self._role_binding()
        request = self._request(
            request_bytes,
            job_id=job_id,
            attempt_no=attempt_no,
            source_evidence_refs=source_evidence_refs,
        )

        def process_spec_factory(workspace: Any, _profile: Mapping[str, Any], _manifest: Mapping[str, Any]) -> ProcessSpec:
            input_path = workspace.input_root / "request.json"
            prestart_path = workspace.output_root / f"{attempt_id}.model_prestart.json"
            terminal_path = workspace.output_root / f"{attempt_id}.model_terminal.json"
            environment = {
                "PROS_P05_T11_PRESTART_PATH": str(prestart_path),
                "PROS_P05_T11_TERMINAL_PATH": str(terminal_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            if os.name == "nt":
                environment.update(
                    {
                        "SYSTEMROOT": os.environ["SYSTEMROOT"],
                        "TEMP": str(workspace.scratch_root),
                        "TMP": str(workspace.scratch_root),
                    }
                )
            return ProcessSpec(
                adapter_id=ADAPTER_ID,
                attempt_id=attempt_id,
                argv=(
                    str(Path(sys.executable).resolve()),
                    str(self._worker_script),
                    "--input",
                    str(input_path),
                    "--prestart",
                    str(prestart_path),
                    "--terminal",
                    str(terminal_path),
                ),
                cwd=str(workspace.scratch_root),
                environment=environment,
                environment_allowlist=_environment_names(),
                timeout_seconds=float(manifest["limits"]["timeout_seconds"]),
                raw_cap_bytes=524288,
                declared_read_paths=(str(self._worker_script), str(input_path)),
                declared_write_paths=(str(prestart_path), str(terminal_path)),
            )

        core_outcome = self._core.execute(
            request,
            role_binding,
            (profile,),
            workspace_parent=workspace_parent,
            job_id=job_id,
            attempt_id=attempt_id,
            predecessor_attempt_ref=retry_of_attempt_id,
            input_materials={"request.json": request_bytes},
            protected_roots=protected_roots,
            process_spec_factory=process_spec_factory,
            payload_schema_ref=OUTPUT_SCHEMA_REF,
            payload_schema=WORKER_RESULT_SCHEMA,
            exact_user_override=PROFILE_ID,
        )
        if core_outcome.workspace_manifest is None or core_outcome.process_receipt is None:
            reasons = core_outcome.selection.get("reason_codes") or ["ROUTE_POLICY_BLOCKED"]
            binding = {
                "attempt_id": attempt_id,
                "logical_operation_id": f"local_embedding_{job_id}",
                "attempt_no": attempt_no,
                "retry_of_attempt_id": retry_of_attempt_id,
                "task_id": "P05_T11",
                "stage": stage,
                "paper_id": None,
                "profile_ref": None,
                "axes": {
                    "process_transport": "none",
                    "inference_location": "unknown",
                    "access_mode": "unknown",
                    "billing_mode": "unknown",
                },
                "evidence_class": "blocked_before_start",
                "provider": None,
                "requested_model": None,
                "route": None,
                "region": None,
                "egress": None,
                "behavior_hash": None,
                "input_hash": None,
                "accounting": {
                    "usage_status": "NOT_INCURRED_BEFORE_START",
                    "token_usage": {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None},
                    "cost_status": "NOT_INCURRED_BEFORE_START",
                    "cost_value": None,
                    "currency": None,
                    "local_resource_status": "NOT_INCURRED_BEFORE_START",
                    "local_resource_metrics": {},
                },
                "blocked_reason_codes": list(reasons),
                "source_evidence_refs": list(source_evidence_refs),
            }
            return LocalEmbeddingOutcome(False, "ROUTE_POLICY_BLOCKED", (), False, core_outcome, None, core_outcome.handoff, binding, manifest)

        output_root = Path(core_outcome.workspace_manifest["output_root"])
        prestart_path = output_root / f"{attempt_id}.model_prestart.json"
        terminal_path = output_root / f"{attempt_id}.model_terminal.json"
        sidecar_error: str | None = None
        try:
            prestart = self._read_json_if_present(prestart_path)
        except LocalSeamViolation as exc:
            prestart = None
            sidecar_error = exc.code
        try:
            terminal_sidecar = self._read_json_if_present(terminal_path)
        except LocalSeamViolation as exc:
            terminal_sidecar = None
            sidecar_error = sidecar_error or exc.code
        payload = core_outcome.normalized_result.get("payload", {}) if core_outcome.normalized_result else {}
        request_started = bool(
            (prestart and prestart.get("request_started") is True)
            or prestart_path.is_file()
        )
        vectors: tuple[tuple[float, ...], ...] = ()
        semantic_error: str | None = None
        try:
            if not core_outcome.success:
                core_error = (
                    core_outcome.normalized_result.get("error_code")
                    if core_outcome.normalized_result
                    else None
                )
                raise LocalSeamViolation(core_error or "LOCAL_PROCESS_FAILED")
            vectors = self._validate_vectors(payload, manifest, len(text_list))
            if sidecar_error is not None:
                raise LocalSeamViolation(sidecar_error)
            self._validate_sidecars(
                prestart,
                terminal_sidecar,
                payload,
                manifest,
                behavior_hash=behavior_hash,
                input_hash=input_hash,
                request_body_sha256=request_body_sha256,
            )
        except LocalSeamViolation as exc:
            semantic_error = exc.code
        success = semantic_error is None
        process_receipt = core_outcome.process_receipt
        if process_receipt.get("timeout"):
            terminal_state = "TIMEOUT"
            error_code = "PROCESS_TIMEOUT"
        elif process_receipt.get("killed"):
            terminal_state = "KILLED"
            error_code = process_receipt.get("error_code") or semantic_error or "LOCAL_PROCESS_KILLED"
        elif success:
            terminal_state = "COMPLETED"
            error_code = None
        else:
            terminal_state = "ERROR"
            error_code = semantic_error or payload.get("error_code") or process_receipt.get("error_code") or "LOCAL_EMBEDDING_FAILED"
        output_hash = (
            str(terminal_sidecar.get("output_hash"))
            if terminal_sidecar and terminal_sidecar.get("output_hash")
            else canonical_sha256({"error_code": error_code, "attempt_id": attempt_id})
        )
        model_receipt = None
        if request_started:
            model_receipt = self._model_receipt(
                process_receipt,
                manifest,
                terminal_sidecar,
                behavior_hash=behavior_hash,
                input_hash=input_hash,
                output_hash=output_hash,
                request_started=True,
                success=success,
            )
        terminal = {
            "terminal_state": terminal_state,
            "verification_result": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "execution_result": "SUCCESS" if success else "FAILED",
            "error_code": error_code,
            "result_ref": object_ref(core_outcome.normalized_result) if success else None,
            "physical_process_start_count": 1 if process_receipt["process_started"] else 0,
            "external_request_start_count": 0,
            "token_status": "N/A_LOCAL_COMPUTE",
            "cost_status": "N/A_LOCAL_COMPUTE",
            "attempt_lineage": {
                "attempt_id": attempt_id,
                "predecessor_attempt_ref": retry_of_attempt_id,
            },
        }
        objects: list[tuple[str, Mapping[str, Any]]] = [
            ("selection", core_outcome.selection),
            ("workspace", core_outcome.workspace_manifest),
            ("process", process_receipt),
        ]
        if model_receipt is not None:
            objects.append(("model", model_receipt))
        assert core_outcome.normalized_result is not None
        objects.append(("schema", core_outcome.normalized_result))
        handoff = self._handoff.build(
            job_id=job_id,
            attempt_id=attempt_id,
            objects=objects,
            terminal=terminal,
        )
        binding = {
            "attempt_id": attempt_id,
            "logical_operation_id": f"local_embedding_{job_id}",
            "attempt_no": attempt_no,
            "retry_of_attempt_id": retry_of_attempt_id,
            "task_id": "P05_T11",
            "stage": stage,
            "paper_id": None,
            "profile_ref": object_ref(profile),
            "axes": {
                "process_transport": "local_process",
                "inference_location": "local_machine",
                "access_mode": "local_runtime",
                "billing_mode": "local_compute",
            },
            "evidence_class": "local_compute",
            "provider": "ollama",
            "requested_model": manifest["model"]["requested_model"],
            "route": "loopback_http",
            "region": "local_machine",
            "egress": "loopback_only",
            "behavior_hash": behavior_hash,
            "input_hash": input_hash,
            "accounting": {
                "usage_status": "N/A_LOCAL_COMPUTE",
                "token_usage": {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "total_tokens": None},
                "cost_status": "N/A_LOCAL_COMPUTE",
                "cost_value": None,
                "currency": None,
                "local_resource_status": "UNKNOWN",
                "local_resource_metrics": {},
            },
            "blocked_reason_codes": [],
            "source_evidence_refs": list(source_evidence_refs) + [manifest["manifest_id"]],
        }
        return LocalEmbeddingOutcome(success, error_code, vectors, request_started, core_outcome, model_receipt, handoff, binding, manifest)
