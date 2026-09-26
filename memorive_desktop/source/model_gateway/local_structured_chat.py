"""Loopback-only structured Ollama chat transport for EVALUATION-ASSETS.

The adapter binds an exact installed model digest before every model request,
disables redirects and fallback, and returns a per-attempt receipt.  It is a
transport primitive only: MODEL_EVALUATION remains responsible for pack custody and local
Gold scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator


_HEX64 = re.compile(r"^[A-Fa-f0-9]{64}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_FORBIDDEN_SUBJECT_MARKERS = re.compile(
    r"(?i)(?:\bgold\b|\bscorer\b|\banswer[_ -]?key\b|\bexpected[_ -]?answer\b)"
)


class LocalStructuredChatViolation(ValueError):
    """A stable fail-closed local transport error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LocalStructuredChatOutcome:
    success: bool
    error_code: str | None
    response: dict[str, Any] | None
    request_projection: dict[str, Any] | None
    receipt: dict[str, Any]
    raw_response: dict[str, Any] | None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise LocalStructuredChatViolation("LOCAL_ENDPOINT_REDIRECT_FORBIDDEN")


def _default_open(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalStructuredChatViolation("CANONICAL_JSON_INVALID") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _derived_endpoint(chat_endpoint: str, path: str) -> str:
    parsed = urlsplit(chat_endpoint)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _read_json_response(response: Any) -> dict[str, Any]:
    with response:
        raw = response.read()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalStructuredChatViolation("LOCAL_RESPONSE_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise LocalStructuredChatViolation("LOCAL_RESPONSE_OBJECT_INVALID")
    return dict(value)


def _safe_http_error_code(error: urllib.error.HTTPError) -> str:
    """Project a stable reason without persisting the server response body."""
    try:
        body = error.read(4096)
    except (OSError, ValueError):
        body = b""
    detail = body.decode("utf-8", errors="replace").lower()
    if error.code == 400:
        if "json schema conversion failed" in detail and "unrecognized schema" in detail:
            return "LOCAL_STRUCTURED_SCHEMA_UNSUPPORTED"
        return "LOCAL_STRUCTURED_REQUEST_REJECTED"
    return f"HTTP_{error.code}"


def validate_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    binding = dict(value)
    if binding.get("schema_version") != "EvaluationAssetsLocalStructuredChatBinding-v1":
        raise LocalStructuredChatViolation("BINDING_VERSION_INVALID")
    endpoint = binding.get("endpoint")
    if not isinstance(endpoint, str):
        raise LocalStructuredChatViolation("ENDPOINT_INVALID")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.path != "/api/chat"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalStructuredChatViolation("NON_LOOPBACK_OR_UNEXPECTED_ENDPOINT")
    model = binding.get("requested_model")
    if not isinstance(model, str) or not model.strip() or model != model.strip():
        raise LocalStructuredChatViolation("MODEL_ID_INVALID")
    digest = binding.get("expected_model_digest")
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        raise LocalStructuredChatViolation("MODEL_DIGEST_INVALID")
    if binding.get("think") is not False:
        raise LocalStructuredChatViolation("THINKING_MUST_BE_DISABLED")
    if binding.get("temperature") != 0:
        raise LocalStructuredChatViolation("TEMPERATURE_MUST_BE_ZERO")
    seed = binding.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise LocalStructuredChatViolation("SEED_INVALID")
    # Memorive does not invent one universal context/output ceiling for every local
    # model.  When these values are supplied they come from the selected saved
    # profile (and therefore from that model/service's declared capacity).  An
    # omitted value deliberately lets the local runtime choose its native
    # default.
    for field in ("num_ctx", "num_predict"):
        number = binding.get(field)
        if number is None:
            continue
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise LocalStructuredChatViolation(f"{field.upper()}_INVALID")
    if binding.get("cloud_fallback") is not False:
        raise LocalStructuredChatViolation("CLOUD_FALLBACK_FORBIDDEN")
    return binding


def _failure_receipt(
    *,
    code: str,
    binding: Mapping[str, Any] | None,
    projection: Mapping[str, Any] | None,
    model_digest: str | None,
    ollama_version: str | None,
    returned_model: str | None,
    metadata_requests: int,
    model_requests: int,
    latency_ms: float | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1",
        "status": "FAIL",
        "error_code": code,
        "requested_model": binding.get("requested_model") if binding else None,
        "returned_model": returned_model,
        "model_digest": model_digest,
        "ollama_version": ollama_version,
        "endpoint": binding.get("endpoint") if binding else None,
        "projection_sha256": projection.get("projection_sha256") if projection else None,
        "latency_ms": latency_ms,
        "local_metadata_request_count": metadata_requests,
        "local_model_request_count": model_requests,
        "external_request_count": 0,
        "cloud_fallback_count": 0,
        "api_cost_cny": 0.0,
        "production_mutation_count": 0,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


class LocalStructuredChatAdapter:
    def __init__(self, *, urlopen: Callable[..., Any] | None = None, clock: Callable[[], float] | None = None):
        self._urlopen = urlopen or _default_open
        self._clock = clock or time.perf_counter

    def execute(
        self,
        *,
        prompt: str,
        schema: Mapping[str, Any],
        binding: Mapping[str, Any],
        purpose: str,
        timeout_seconds: float | None = None,
    ) -> LocalStructuredChatOutcome:
        accepted_binding: dict[str, Any] | None = None
        projection: dict[str, Any] | None = None
        raw: dict[str, Any] | None = None
        model_digest: str | None = None
        ollama_version: str | None = None
        returned_model: str | None = None
        metadata_requests = 0
        model_requests = 0
        latency_ms: float | None = None
        try:
            accepted_binding = validate_binding(binding)
            if not isinstance(prompt, str) or not prompt.strip():
                raise LocalStructuredChatViolation("PROMPT_INVALID")
            if _FORBIDDEN_SUBJECT_MARKERS.search(prompt):
                raise LocalStructuredChatViolation("EVALUATOR_DATA_MARKER_FORBIDDEN")
            if not isinstance(schema, Mapping):
                raise LocalStructuredChatViolation("SCHEMA_OBJECT_REQUIRED")
            Draft202012Validator.check_schema(dict(schema))
            if not isinstance(purpose, str) or not purpose.strip():
                raise LocalStructuredChatViolation("PURPOSE_INVALID")

            tags_request = urllib.request.Request(
                _derived_endpoint(accepted_binding["endpoint"], "/api/tags"), method="GET"
            )
            metadata_requests += 1
            tags = _read_json_response(self._urlopen(tags_request, timeout=min(timeout_seconds, 10) if timeout_seconds is not None else 10))
            installed = [row for row in tags.get("models", []) if isinstance(row, Mapping)]
            matched = next(
                (
                    row
                    for row in installed
                    if (row.get("model") or row.get("name")) == accepted_binding["requested_model"]
                ),
                None,
            )
            if matched is None:
                raise LocalStructuredChatViolation("MODEL_NOT_PRESENT")
            model_digest = str(matched.get("digest", ""))
            if model_digest.lower() != accepted_binding["expected_model_digest"].lower():
                raise LocalStructuredChatViolation("MODEL_IDENTITY_MISMATCH")

            version_request = urllib.request.Request(
                _derived_endpoint(accepted_binding["endpoint"], "/api/version"), method="GET"
            )
            metadata_requests += 1
            version = _read_json_response(self._urlopen(version_request, timeout=min(timeout_seconds, 10) if timeout_seconds is not None else 10))
            if not isinstance(version.get("version"), str) or not version["version"]:
                raise LocalStructuredChatViolation("OLLAMA_VERSION_MISSING")
            ollama_version = version["version"]

            schema_object = dict(schema)
            options = {
                "temperature": 0,
                "seed": accepted_binding["seed"],
            }
            for optional_parameter in ("num_ctx", "num_predict"):
                if accepted_binding.get(optional_parameter) is not None:
                    options[optional_parameter] = accepted_binding[optional_parameter]
            body = {
                "model": accepted_binding["requested_model"],
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "keep_alive": 0,
                "format": schema_object,
                "options": options,
            }
            projection = {
                "schema_version": "EvaluationAssetsLocalStructuredChatProjection-v1",
                "purpose": purpose,
                "endpoint": accepted_binding["endpoint"],
                "requested_model": accepted_binding["requested_model"],
                "expected_model_digest": accepted_binding["expected_model_digest"].upper(),
                "observed_model_digest": model_digest.upper(),
                "ollama_version": ollama_version,
                "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
                "schema_sha256": _canonical_sha256(schema_object),
                "parameters": {
                    "stream": False,
                    "think": False,
                    "keep_alive": 0,
                    "temperature": 0,
                    "seed": accepted_binding["seed"],
                    "num_ctx": accepted_binding.get("num_ctx"),
                    "num_predict": accepted_binding.get("num_predict"),
                    "num_ctx_source": (
                        "SAVED_VERIFIED_PROFILE"
                        if accepted_binding.get("num_ctx") is not None
                        else "OLLAMA_RUNTIME_DEFAULT"
                    ),
                },
                "gold_or_scorer_present": False,
                "external_egress": False,
                "cloud_fallback": False,
            }
            projection["projection_sha256"] = _canonical_sha256(projection)

            started = self._clock()
            chat_request = urllib.request.Request(
                accepted_binding["endpoint"],
                data=_canonical_bytes(body),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            model_requests += 1
            raw = _read_json_response(self._urlopen(chat_request, timeout=timeout_seconds))
            latency_ms = round((self._clock() - started) * 1000, 3)
            returned_model = raw.get("model") if isinstance(raw.get("model"), str) else None
            if returned_model != accepted_binding["requested_model"]:
                raise LocalStructuredChatViolation("RETURNED_MODEL_MISMATCH")
            if raw.get('done_reason') in {'length', 'max_tokens'}:
                raise LocalStructuredChatViolation('MODEL_OUTPUT_TRUNCATED')
            message = raw.get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if not isinstance(content, str) or not content.strip():
                raise LocalStructuredChatViolation("MODEL_CONTENT_MISSING")
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LocalStructuredChatViolation("MODEL_CONTENT_JSON_INVALID") from exc
            if not isinstance(parsed, Mapping):
                raise LocalStructuredChatViolation("MODEL_CONTENT_OBJECT_REQUIRED")
            diagnostics = sorted(
                Draft202012Validator(schema_object).iter_errors(parsed),
                key=lambda item: list(item.absolute_path),
            )
            if diagnostics:
                raise LocalStructuredChatViolation("MODEL_CONTENT_SCHEMA_INVALID")
            response = dict(parsed)
            receipt: dict[str, Any] = {
                "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1",
                "status": "PASS",
                "error_code": None,
                "purpose": purpose,
                "requested_model": accepted_binding["requested_model"],
                "returned_model": returned_model,
                "model_digest": model_digest.upper(),
                "ollama_version": ollama_version,
                "endpoint": accepted_binding["endpoint"],
                "projection_sha256": projection["projection_sha256"],
                "response_sha256": _canonical_sha256(response),
                "parameters": projection["parameters"],
                "usage": {
                    key: raw.get(key)
                    for key in (
                        "prompt_eval_count",
                        "eval_count",
                        "total_duration",
                        "load_duration",
                        "prompt_eval_duration",
                        "eval_duration",
                    )
                },
                "latency_ms": latency_ms,
                "local_metadata_request_count": metadata_requests,
                "local_model_request_count": model_requests,
                "external_request_count": 0,
                "cloud_fallback_count": 0,
                "api_cost_cny": 0.0,
                "production_mutation_count": 0,
            }
            receipt["receipt_sha256"] = _canonical_sha256(receipt)
            return LocalStructuredChatOutcome(True, None, response, projection, receipt, raw)
        except (
            LocalStructuredChatViolation,
            OSError,
            urllib.error.URLError,
            ValueError,
        ) as exc:
            if isinstance(exc, LocalStructuredChatViolation):
                code = exc.code
            elif isinstance(exc, urllib.error.HTTPError):
                code = _safe_http_error_code(exc)
            else:
                code = type(exc).__name__
            receipt = _failure_receipt(
                code=code,
                binding=accepted_binding or (binding if isinstance(binding, Mapping) else None),
                projection=projection,
                model_digest=model_digest,
                ollama_version=ollama_version,
                returned_model=returned_model,
                metadata_requests=metadata_requests,
                model_requests=model_requests,
                latency_ms=latency_ms,
            )
            if isinstance(raw, Mapping):
                receipt['usage'] = {key: raw.get(key) for key in ('prompt_eval_count', 'eval_count', 'total_duration')}
                receipt['finish_reason'] = raw.get('done_reason')
                receipt['capacity_assessment'] = 'NOT_ASSESSED' if code == 'MODEL_OUTPUT_TRUNCATED' else None
                receipt['receipt_sha256'] = _canonical_sha256({key: value for key, value in receipt.items() if key != 'receipt_sha256'})
            return LocalStructuredChatOutcome(False, code, None, projection, receipt, raw)
