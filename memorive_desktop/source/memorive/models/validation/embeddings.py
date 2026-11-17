"""Embedding requests: _EmbeddingExecutionMixin.execute_embeddings, part of LiveModelValidationRunner,
sends texts to an API or a local embedding server. A text too long for a local model is split, and the
parts' vectors are combined into one normalized vector weighted by their sizes.
"""
from __future__ import annotations

from memorive.models.calls.ledger import recorded_execution
from dataclasses import dataclass, field
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


@dataclass
class _EmbeddingAttempt:
    """One embedding request while it runs (execute_embeddings).

    The request is set when it is made. Preparation (_prepare_embedding_api, _prepare_embedding_local)
    sets where the batches go; sending (_send_embeddings_api, _send_embeddings_local) adds the vectors,
    counts, usage and response hashes batch by batch; _embedding_result builds the result. A failure is
    reported from the counts as they are."""

    # The request.
    profile_kind: str
    service: Mapping[str, Any]
    purpose: str
    timeout_seconds: int
    requested_model: str
    accepted_inputs: list[str]
    behavior_sha256: str
    started: float
    batch_size: int
    returned_model: str | None
    # Where the batches go, set by preparation.
    endpoint: str = ""
    opener: Any = None
    proxy_mode: str = ""
    proxy_address: str = ""
    # The API credential while the batches are sent; cleared at the end.
    secret: str = ""
    secret_bytes: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    # What the batches return, kept for a failure too.
    vectors: list[list[float]] = field(default_factory=list)
    physical_calls: int = 0
    metadata_calls: int = 0
    usage_totals: dict[str, int] = field(default_factory=dict)
    response_hashes: list[str] = field(default_factory=list)
    adaptive_split_events: list[dict[str, Any]] = field(default_factory=list)


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
        attempt = _EmbeddingAttempt(
            profile_kind=profile_kind, service=service, purpose=purpose,
            timeout_seconds=timeout_seconds, requested_model=requested_model,
            accepted_inputs=accepted_inputs, behavior_sha256=behavior_sha256, started=time.monotonic(),
            batch_size=256 if requested_model in {"BAAI/bge-m3", "Pro/BAAI/bge-m3"} else 32,
            returned_model=requested_model if profile_kind == "LOCAL" else None,
            usage_totals={"prompt_tokens": 0, "completion_tokens": 0},
        )
        try:
            if attempt.profile_kind == "API":
                self._prepare_embedding_api(attempt)
            else:
                self._prepare_embedding_local(attempt)

            if attempt.profile_kind == "LOCAL":
                self._send_embeddings_local(attempt)
            else:
                self._send_embeddings_api(attempt)
            return self._embedding_result(attempt)
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
                "requested_model": attempt.requested_model,
                "returned_model": attempt.returned_model,
                "duration_ms": max(0, round((time.monotonic() - attempt.started) * 1000)),
                "external_network_calls": attempt.physical_calls if attempt.profile_kind == "API" else 0,
                "provider_calls": attempt.physical_calls if attempt.profile_kind == "API" else 0,
                "external_model_calls": attempt.physical_calls,
                "external_process_launches": 0,
                "local_metadata_calls": attempt.metadata_calls,
            }
            if isinstance(error, _LocalEmbeddingHttpFailure):
                failed["execution_receipt"] = {
                    "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                    "status": "FAILED",
                    "profile_kind": attempt.profile_kind,
                    "purpose": attempt.purpose,
                    "route": "OLLAMA_LOOPBACK",
                    "region": "LOCAL_MACHINE",
                    "egress": "LOOPBACK_ONLY",
                    "requested_model": attempt.requested_model,
                    "returned_model": attempt.returned_model,
                    "model_digest": attempt.service.get("model_digest"),
                    "behavior_sha256": attempt.behavior_sha256,
                    "actual_cost": None,
                    "estimated_cost_cny": 0.0,
                    "cost_evidence": "LOCAL_COMPUTE_NO_PROVIDER_API_COST",
                    "transport_diagnostic": error.diagnostic,
                }
            return failed
        finally:
            attempt.secret = ""
            attempt.secret_bytes = b""
            attempt.headers.clear()

    def _prepare_embedding_api(self, attempt: _EmbeddingAttempt) -> None:
        """The API endpoint, the credential header and the proxy setting for the batches."""
        profile = _api_profile(attempt.service)
        if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
            raise ValueError("EMBEDDING_API_PROFILE_UNSUPPORTED")
        parsed = urlsplit(profile.url)
        path = parsed.path.rstrip("/")
        if not path.casefold().endswith("/models"):
            raise ValueError("EMBEDDING_API_ENDPOINT_INVALID")
        attempt.endpoint = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                path[: -len("/models")] + "/embeddings",
                "",
                "",
            )
        )
        credential_ref = attempt.service.get("credential_ref")
        if not isinstance(credential_ref, str) or not credential_ref:
            raise ValueError("API_CREDENTIAL_UNAVAILABLE")
        attempt.secret_bytes = self._credential_resolver(credential_ref)
        if not attempt.secret_bytes or len(attempt.secret_bytes) > 512:
            raise ValueError("API_CREDENTIAL_UNAVAILABLE")
        attempt.secret = attempt.secret_bytes.decode("utf-8", errors="strict")
        attempt.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Memorive-Embedding-Exam/1",
            **dict(profile.extra_headers),
        }
        attempt.headers[profile.auth_header] = (
            f"Bearer {attempt.secret}"
            if profile.auth_header == "Authorization"
            else attempt.secret
        )
        attempt.proxy_mode, attempt.proxy_address, _ = self.preferences()

    def _prepare_embedding_local(self, attempt: _EmbeddingAttempt) -> None:
        """The local Ollama embedding endpoint, checked against the profile's exact model and digest."""
        if (
            attempt.service.get("endpoint_kind") != "ollama"
            or attempt.service.get("execution_eligible") is not True
            or attempt.service.get("exact_identity_available") is not True
            or not isinstance(attempt.service.get("chat_endpoint"), str)
            or not isinstance(attempt.service.get("model_digest"), str)
            or not SAFE_SHA256.fullmatch(
                str(attempt.service["model_digest"]).upper()
            )
        ):
            raise ValueError("LOCAL_EMBEDDING_PROFILE_INVALID")
        parsed = urlsplit(str(attempt.service["chat_endpoint"]))
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
        attempt.endpoint = urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/embed", "", "")
        )
        tags_endpoint = urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/tags", "", "")
        )
        attempt.opener = build_opener(ProxyHandler({}), _NoRedirect())
        attempt.metadata_calls += 1
        from memorive.models.calls.ledger import recording_urlopen
        attempt.opener.open = recording_urlopen(attempt.opener.open)
        with attempt.opener.open(
            Request(tags_endpoint, method="GET"), timeout=min(10, attempt.timeout_seconds)
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
                == attempt.requested_model
            ),
            None,
        )
        if (
            not isinstance(matched, Mapping)
            or str(matched.get("digest") or "").upper()
            != str(attempt.service["model_digest"]).upper()
        ):
            raise ValueError("LOCAL_EMBEDDING_MODEL_IDENTITY_MISMATCH")

    def _send_embeddings_local(self, attempt: _EmbeddingAttempt) -> None:
        """Send the inputs to the local model batch by batch."""
        def local_vector(value: str, *, depth: int = 0) -> list[float]:
            payload = {
                "model": attempt.requested_model,
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
                attempt.endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            attempt.physical_calls += 1
            try:
                with attempt.opener.open(request, timeout=attempt.timeout_seconds) as response:
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
                    attempt.adaptive_split_events.append(
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
            attempt.response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
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
                attempt.usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                attempt.usage_totals["completion_tokens"] += normalised[
                    "completion_tokens"
                ]
            return batch_vectors[0]

        for value in attempt.accepted_inputs:
            attempt.vectors.append(local_vector(value))

    def _send_embeddings_api(self, attempt: _EmbeddingAttempt) -> None:
        """Send the inputs to the API batch by batch, splitting a batch the provider refuses as too large."""
        for start in range(0, len(attempt.accepted_inputs), attempt.batch_size):
            batch = attempt.accepted_inputs[start : start + attempt.batch_size]
            body = json.dumps(
                {
                    "model": attempt.requested_model,
                    "input": batch,
                    "encoding_format": "float",
                },
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            attempt.physical_calls += 1
            response = self._http.request(
                method="POST",
                url=attempt.endpoint,
                headers=attempt.headers,
                body=body,
                timeout_seconds=attempt.timeout_seconds,
                proxy_mode=attempt.proxy_mode,
                proxy_address=attempt.proxy_address,
            )
            status_code = response.status_code
            raw = response.body
            if status_code != 200:
                raise ValueError(f"EMBEDDING_HTTP_{status_code}")
            attempt.response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, Mapping):
                raise ValueError("EMBEDDING_RESPONSE_INVALID")
            observed_model = document.get("model")
            if observed_model != attempt.requested_model:
                raise ValueError("EMBEDDING_RETURNED_MODEL_MISMATCH")
            attempt.returned_model = str(observed_model)
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
            attempt.vectors.extend(batch_vectors)
            raw_usage = (
                document.get("usage")
                if isinstance(document.get("usage"), Mapping)
                else document
            )
            normalised = _normalise_token_usage(raw_usage)
            if normalised:
                attempt.usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                attempt.usage_totals["completion_tokens"] += normalised[
                    "completion_tokens"
                ]

    def _embedding_result(self, attempt: _EmbeddingAttempt) -> Any:
        """The result: one vector per input, the usage, the estimated cost and the receipt."""
        if len(attempt.vectors) != len(attempt.accepted_inputs):
            raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
        input_price_cny_per_million = {"BAAI/bge-m3": 0.07, "Pro/BAAI/bge-m3": 0.07, "Qwen/Qwen3-VL-Embedding-8B": 0.70}.get(attempt.requested_model)
        estimated_cost_cny = (
            round(
                (
                    attempt.usage_totals["prompt_tokens"]
                    if attempt.usage_totals["prompt_tokens"]
                    else sum(len(value.encode("utf-8")) for value in attempt.accepted_inputs)
                )
                * input_price_cny_per_million
                / 1_000_000,
                12,
            )
            if attempt.profile_kind == "API"
            and input_price_cny_per_million is not None
            else (0.0 if attempt.profile_kind == "LOCAL" else None)
        )
        duration_ms = max(0, round((time.monotonic() - attempt.started) * 1000))
        return {
            "schema_version": "SettingsEmbeddingRunnerResult-v1",
            "status": "PASS",
            "reason": "EMBEDDING_COMPLETED",
            "requested_model": attempt.requested_model,
            "returned_model": attempt.returned_model,
            "vectors": attempt.vectors,
            "duration_ms": duration_ms,
            "external_network_calls": attempt.physical_calls if attempt.profile_kind == "API" else 0,
            "provider_calls": attempt.physical_calls if attempt.profile_kind == "API" else 0,
            "external_model_calls": attempt.physical_calls,
            "external_process_launches": 0,
            "local_metadata_calls": attempt.metadata_calls,
            "execution_receipt": {
                "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                "status": "PASS",
                "profile_kind": attempt.profile_kind,
                "purpose": attempt.purpose,
                "route": (
                    "EXISTING_SETTINGS_API_TRANSPORT"
                    if attempt.profile_kind == "API"
                    else "OLLAMA_LOOPBACK"
                ),
                "region": (
                    "PROVIDER_MANAGED_UNDISCLOSED"
                    if attempt.profile_kind == "API"
                    else "LOCAL_MACHINE"
                ),
                "egress": (
                    "PROVIDER_API"
                    if attempt.profile_kind == "API"
                    else "LOOPBACK_ONLY"
                ),
                "requested_model": attempt.requested_model,
                "returned_model": attempt.returned_model,
                "model_digest": attempt.service.get("model_digest"),
                "model_identity_evidence": (
                    "PROVIDER_RESPONSE_MODEL_FIELD"
                    if attempt.profile_kind == "API"
                    else "OLLAMA_EXACT_NAME_AND_DIGEST_PRECALL_BINDING"
                ),
                "behavior_sha256": attempt.behavior_sha256,
                "response_sha256": hashlib.sha256(
                    "".join(attempt.response_hashes).encode("ascii")
                ).hexdigest().upper(),
                "token_usage": attempt.usage_totals,
                "actual_cost": None,
                "estimated_cost_cny": estimated_cost_cny,
                "cost_evidence": (
                    "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
                    if attempt.profile_kind == "API"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                ),
                "adaptive_embedding_split": {
                    "activated": bool(attempt.adaptive_split_events),
                    "split_event_count": len(attempt.adaptive_split_events),
                    "events": attempt.adaptive_split_events,
                    "aggregation": (
                        "CHARACTER_WEIGHTED_MEAN_THEN_L2_NORMALIZE"
                        if attempt.adaptive_split_events
                        else None
                    ),
                },
                "request_input_limit": 256,
                "physical_batch_size": attempt.batch_size if attempt.profile_kind == "API" else 1,
                "physical_batch_count": attempt.physical_calls,
                "max_response_bytes": None,
            },
        }
