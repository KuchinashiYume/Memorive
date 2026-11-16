"""Embedding requests: _EmbeddingExecutionMixin.execute_embeddings, part of LiveModelValidationRunner,
sends texts to an API or a local embedding server. A text too long for a local model is split, and the
parts' vectors are combined into one normalized vector weighted by their sizes.
"""
from __future__ import annotations

from memorive.models.calls.ledger import recorded_execution
import hashlib
import json
import math
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener
from memorive.models.endpoints import SAFE_MODEL, SAFE_SHA256, _api_profile

from memorive.models.validation.transport import _NoRedirect
from memorive.models.validation.usage import _normalise_token_usage


class _LocalEmbeddingHttpFailure(RuntimeError):
    def __init__(self, reason: str, diagnostic: Mapping[str, Any]):
        self.reason = reason
        self.diagnostic = dict(diagnostic)
        super().__init__(reason)


def _local_embedding_http_diagnostic(status: int, body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").casefold()
    physical_batch = (
        "physical batch size" in text
        and ("too large to process" in text or "increase" in text)
    )
    context_exceeded = status == 400 and any(marker in text for marker in (
        'input length exceeds the context length', 'input length exceeds maximum context length',
    ))
    classification = (
        "PHYSICAL_BATCH_TOO_SMALL"
        if physical_batch
        else ('INPUT_CONTEXT_EXCEEDED' if context_exceeded else
              ("UNCLASSIFIED_RETRYABLE" if status == 400 else "HTTP_FAILURE"))
    )
    return {
        "http_status": status,
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest().upper(),
        "classification": classification,
        "physical_batch_marker": physical_batch,
        "context_length_marker": context_exceeded,
        "raw_body_recorded": False,
    }


def _split_embedding_text(value: str) -> tuple[str, str]:
    if len(value) < 2:
        raise ValueError("LOCAL_EMBEDDING_TEXT_CANNOT_SPLIT")
    midpoint = len(value) // 2
    radius = max(32, len(value) // 4)
    lower = max(1, midpoint - radius)
    upper = min(len(value) - 1, midpoint + radius)
    boundaries = [
        index + 1
        for index in range(lower, upper)
        if value[index] in "\n。！？.!?;； "
    ]
    split_at = min(boundaries, key=lambda index: abs(index - midpoint)) if boundaries else midpoint
    left, right = value[:split_at].strip(), value[split_at:].strip()
    if not left or not right:
        left, right = value[:midpoint], value[midpoint:]
    if not left or not right:
        raise ValueError("LOCAL_EMBEDDING_TEXT_CANNOT_SPLIT")
    return left, right


def _weighted_normalized_vector(
    vectors: Sequence[Sequence[float]], weights: Sequence[int]
) -> list[float]:
    if not vectors or len(vectors) != len(weights):
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_VECTOR_SET_INVALID")
    dimension = len(vectors[0])
    if dimension <= 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_DIMENSION_MISMATCH")
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_WEIGHT_INVALID")
    combined = [
        sum(float(vector[index]) * weight for vector, weight in zip(vectors, weights, strict=True))
        / total_weight
        for index in range(dimension)
    ]
    norm = math.sqrt(sum(value * value for value in combined))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_VECTOR_NORM_INVALID")
    return [value / norm for value in combined]


class _EmbeddingExecutionMixin:
    """Live API/local embedding transport shared only by the desktop runner."""

    @staticmethod
    def _not_run(kind: str) -> dict[str, Any]:
        return {
            "schema_version": "SettingsModelValidationResult-v1",
            "kind": kind,
            "status": "NOT_RUN",
            "score": None,
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
        }

    @recorded_execution
    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        if profile_kind not in {"API", "LOCAL"}:
            raise ValueError("EMBEDDING_PROFILE_KIND_INVALID")
        requested_model = model.get("model_name")
        accepted_inputs = list(inputs)
        if (
            not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not accepted_inputs
            or len(accepted_inputs) > 256
            or any(
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 65536
                for value in accepted_inputs
            )
            or purpose not in {"workflow_model_exam", "core_document_processing", "research_retrieval"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
        ):
            raise ValueError("EMBEDDING_REQUEST_INVALID")
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "model": requested_model,
                    "inputs": accepted_inputs,
                    "purpose": purpose,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        started = time.monotonic()
        vectors: list[list[float]] = []
        physical_calls = 0
        metadata_calls = 0
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0}
        response_hashes: list[str] = []
        adaptive_split_events: list[dict[str, Any]] = []
        batch_size = 256 if requested_model in {"BAAI/bge-m3", "Pro/BAAI/bge-m3"} else 32
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        try:
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("EMBEDDING_API_PROFILE_UNSUPPORTED")
                parsed = urlsplit(profile.url)
                path = parsed.path.rstrip("/")
                if not path.casefold().endswith("/models"):
                    raise ValueError("EMBEDDING_API_ENDPOINT_INVALID")
                endpoint = urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        path[: -len("/models")] + "/embeddings",
                        "",
                        "",
                    )
                )
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-Embedding-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}"
                    if profile.auth_header == "Authorization"
                    else secret
                )
                proxy_mode, proxy_address, _ = self.preferences()
            else:
                if (
                    service.get("endpoint_kind") != "ollama"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("chat_endpoint"), str)
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(
                        str(service["model_digest"]).upper()
                    )
                ):
                    raise ValueError("LOCAL_EMBEDDING_PROFILE_INVALID")
                parsed = urlsplit(str(service["chat_endpoint"]))
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path != "/api/chat"
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_EMBEDDING_ENDPOINT_INVALID")
                endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "/api/embed", "", "")
                )
                tags_endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "/api/tags", "", "")
                )
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                metadata_calls += 1
                from memorive.models.calls.ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                with opener.open(
                    Request(tags_endpoint, method="GET"), timeout=min(10, timeout_seconds)
                ) as response:
                    tags_raw = response.read(2 * 1024 * 1024 + 1)
                if len(tags_raw) > 2 * 1024 * 1024:
                    raise ValueError("LOCAL_EMBEDDING_TAGS_RESPONSE_TOO_LARGE")
                tags = json.loads(tags_raw.decode("utf-8"))
                rows = tags.get("models") if isinstance(tags, Mapping) else None
                matched = next(
                    (
                        row
                        for row in (rows if isinstance(rows, list) else [])
                        if isinstance(row, Mapping)
                        and (row.get("model") or row.get("name"))
                        == requested_model
                    ),
                    None,
                )
                if (
                    not isinstance(matched, Mapping)
                    or str(matched.get("digest") or "").upper()
                    != str(service["model_digest"]).upper()
                ):
                    raise ValueError("LOCAL_EMBEDDING_MODEL_IDENTITY_MISMATCH")

            if profile_kind == "LOCAL":
                def local_vector(value: str, *, depth: int = 0) -> list[float]:
                    nonlocal physical_calls
                    payload = {
                        "model": requested_model,
                        "input": [value],
                        "truncate": False,
                    }
                    body = json.dumps(
                        payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    request = Request(
                        endpoint,
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    physical_calls += 1
                    try:
                        with opener.open(request, timeout=timeout_seconds) as response:
                            status_code = int(response.status)
                            raw = response.read(8 * 1024 * 1024 + 1)
                    except HTTPError as error:
                        error_body = error.read(64 * 1024 + 1)[: 64 * 1024]
                        diagnostic = _local_embedding_http_diagnostic(
                            int(error.code), error_body
                        )
                        if (
                            diagnostic["classification"] in {"PHYSICAL_BATCH_TOO_SMALL", "INPUT_CONTEXT_EXCEEDED"}
                            and len(value) > 1
                        ):
                            left, right = _split_embedding_text(value)
                            child_vectors = [
                                local_vector(left, depth=depth + 1),
                                local_vector(right, depth=depth + 1),
                            ]
                            adaptive_split_events.append(
                                {
                                    "depth": depth,
                                    "original_chars": len(value),
                                    "child_chars": [len(left), len(right)],
                                    "original_sha256": hashlib.sha256(
                                        value.encode("utf-8")
                                    ).hexdigest().upper(),
                                    "child_sha256s": [
                                        hashlib.sha256(part.encode("utf-8"))
                                        .hexdigest()
                                        .upper()
                                        for part in (left, right)
                                    ],
                                    "trigger": diagnostic,
                                    "source_chunk_identity_preserved": True,
                                }
                            )
                            return _weighted_normalized_vector(
                                child_vectors, [len(left), len(right)]
                            )
                        reason = (
                            "LOCAL_EMBEDDING_HTTP_400_UNCLASSIFIED_RETRYABLE"
                            if diagnostic["classification"]
                            == "UNCLASSIFIED_RETRYABLE"
                            else f"LOCAL_EMBEDDING_HTTP_{error.code}"
                        )
                        raise _LocalEmbeddingHttpFailure(reason, diagnostic) from error
                    if len(raw) > 8 * 1024 * 1024:
                        raise ValueError("LOCAL_EMBEDDING_RESPONSE_TOO_LARGE")
                    if status_code != 200:
                        raise _LocalEmbeddingHttpFailure(
                            f"LOCAL_EMBEDDING_HTTP_{status_code}",
                            _local_embedding_http_diagnostic(status_code, raw),
                        )
                    response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
                    document = json.loads(raw.decode("utf-8"))
                    batch_vectors = (
                        document.get("embeddings")
                        if isinstance(document, Mapping)
                        else None
                    )
                    if (
                        not isinstance(batch_vectors, list)
                        or len(batch_vectors) != 1
                        or not isinstance(batch_vectors[0], list)
                    ):
                        raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
                    raw_usage = (
                        document.get("usage")
                        if isinstance(document.get("usage"), Mapping)
                        else document
                    )
                    normalised = _normalise_token_usage(raw_usage)
                    if normalised:
                        usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                        usage_totals["completion_tokens"] += normalised[
                            "completion_tokens"
                        ]
                    return batch_vectors[0]

                for value in accepted_inputs:
                    vectors.append(local_vector(value))
            else:
                for start in range(0, len(accepted_inputs), batch_size):
                    batch = accepted_inputs[start : start + batch_size]
                    body = json.dumps(
                        {
                            "model": requested_model,
                            "input": batch,
                            "encoding_format": "float",
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    physical_calls += 1
                    response = self._http.request(
                        method="POST",
                        url=endpoint,
                        headers=headers,
                        body=body,
                        timeout_seconds=timeout_seconds,
                        proxy_mode=proxy_mode,
                        proxy_address=proxy_address,
                    )
                    status_code = response.status_code
                    raw = response.body
                    if status_code != 200:
                        raise ValueError(f"EMBEDDING_HTTP_{status_code}")
                    response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
                    document = json.loads(raw.decode("utf-8"))
                    if not isinstance(document, Mapping):
                        raise ValueError("EMBEDDING_RESPONSE_INVALID")
                    observed_model = document.get("model")
                    if observed_model != requested_model:
                        raise ValueError("EMBEDDING_RETURNED_MODEL_MISMATCH")
                    returned_model = str(observed_model)
                    data = document.get("data")
                    if not isinstance(data, list):
                        raise ValueError("EMBEDDING_RESPONSE_INVALID")
                    ordered = sorted(
                        data,
                        key=lambda row: (
                            row.get("index")
                            if isinstance(row, Mapping)
                            and isinstance(row.get("index"), int)
                            else -1
                        ),
                    )
                    batch_vectors = [
                        row.get("embedding")
                        for row in ordered
                        if isinstance(row, Mapping)
                    ]
                    if (
                        len(batch_vectors) != len(batch)
                        or any(not isinstance(row, list) for row in batch_vectors)
                    ):
                        raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
                    vectors.extend(batch_vectors)
                    raw_usage = (
                        document.get("usage")
                        if isinstance(document.get("usage"), Mapping)
                        else document
                    )
                    normalised = _normalise_token_usage(raw_usage)
                    if normalised:
                        usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                        usage_totals["completion_tokens"] += normalised[
                            "completion_tokens"
                        ]
            if len(vectors) != len(accepted_inputs):
                raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
            input_price_cny_per_million = {"BAAI/bge-m3": 0.07, "Pro/BAAI/bge-m3": 0.07, "Qwen/Qwen3-VL-Embedding-8B": 0.70}.get(requested_model)
            estimated_cost_cny = (
                round(
                    (
                        usage_totals["prompt_tokens"]
                        if usage_totals["prompt_tokens"]
                        else sum(len(value.encode("utf-8")) for value in accepted_inputs)
                    )
                    * input_price_cny_per_million
                    / 1_000_000,
                    12,
                )
                if profile_kind == "API"
                and input_price_cny_per_million is not None
                else (0.0 if profile_kind == "LOCAL" else None)
            )
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsEmbeddingRunnerResult-v1",
                "status": "PASS",
                "reason": "EMBEDDING_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "vectors": vectors,
                "duration_ms": duration_ms,
                "external_network_calls": physical_calls if profile_kind == "API" else 0,
                "provider_calls": physical_calls if profile_kind == "API" else 0,
                "external_model_calls": physical_calls,
                "external_process_launches": 0,
                "local_metadata_calls": metadata_calls,
                "execution_receipt": {
                    "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": (
                        "EXISTING_SETTINGS_API_TRANSPORT"
                        if profile_kind == "API"
                        else "OLLAMA_LOOPBACK"
                    ),
                    "region": (
                        "PROVIDER_MANAGED_UNDISCLOSED"
                        if profile_kind == "API"
                        else "LOCAL_MACHINE"
                    ),
                    "egress": (
                        "PROVIDER_API"
                        if profile_kind == "API"
                        else "LOOPBACK_ONLY"
                    ),
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": (
                        "PROVIDER_RESPONSE_MODEL_FIELD"
                        if profile_kind == "API"
                        else "OLLAMA_EXACT_NAME_AND_DIGEST_PRECALL_BINDING"
                    ),
                    "behavior_sha256": behavior_sha256,
                    "response_sha256": hashlib.sha256(
                        "".join(response_hashes).encode("ascii")
                    ).hexdigest().upper(),
                    "token_usage": usage_totals,
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": (
                        "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
                        if profile_kind == "API"
                        else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                    ),
                    "adaptive_embedding_split": {
                        "activated": bool(adaptive_split_events),
                        "split_event_count": len(adaptive_split_events),
                        "events": adaptive_split_events,
                        "aggregation": (
                            "CHARACTER_WEIGHTED_MEAN_THEN_L2_NORMALIZE"
                            if adaptive_split_events
                            else None
                        ),
                    },
                    "request_input_limit": 256,
                    "physical_batch_size": batch_size if profile_kind == "API" else 1,
                    "physical_batch_count": physical_calls,
                    "max_response_bytes": None,
                },
            }
        except Exception as error:
            reason = (
                error.reason
                if isinstance(error, _LocalEmbeddingHttpFailure)
                else (str(error) or type(error).__name__)
            )
            if len(reason) > 160:
                reason = type(error).__name__
            failed = {
                "schema_version": "SettingsEmbeddingRunnerResult-v1",
                "status": "FAILED",
                "reason": reason,
                "requested_model": requested_model,
                "returned_model": returned_model,
                "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
                "external_network_calls": physical_calls if profile_kind == "API" else 0,
                "provider_calls": physical_calls if profile_kind == "API" else 0,
                "external_model_calls": physical_calls,
                "external_process_launches": 0,
                "local_metadata_calls": metadata_calls,
            }
            if isinstance(error, _LocalEmbeddingHttpFailure):
                failed["execution_receipt"] = {
                    "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                    "status": "FAILED",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": "OLLAMA_LOOPBACK",
                    "region": "LOCAL_MACHINE",
                    "egress": "LOOPBACK_ONLY",
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "behavior_sha256": behavior_sha256,
                    "actual_cost": None,
                    "estimated_cost_cny": 0.0,
                    "cost_evidence": "LOCAL_COMPUTE_NO_PROVIDER_API_COST",
                    "transport_diagnostic": error.diagnostic,
                }
            return failed
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()
