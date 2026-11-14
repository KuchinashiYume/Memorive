"""Bounded local Ollama embedding worker for LOCAL-RETRIEVAL.

The worker accepts one create-only JSON request file, asserts an exact loopback
endpoint before opening a socket, writes pre-start/terminal sidecars without
payload text, and emits one strict JSON result on stdout. It never retries,
discovers models, downloads assets, or falls back to a cloud provider.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit


SCHEMA_VERSION = "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_REQUEST_V1"
RESULT_VERSION = "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_RESULT_V1"
WORKER_VERSION = "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_V1"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_FIELDS = {
    "schema_version",
    "endpoint",
    "model",
    "model_manifest_sha256",
    "expected_canonical_model",
    "expected_dimension",
    "normalization",
    "normalization_min",
    "normalization_max",
    "max_batch_size",
    "max_input_utf8_bytes",
    "timeout_seconds",
    "behavior_hash",
    "texts",
}


class WorkerError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


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


def _canonical_model(value: str) -> str:
    return value.rsplit("/", 1)[-1].split(":", 1)[0]


def _write_create_only(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_canonical_bytes(value))


def _validate_endpoint(endpoint: Any) -> str:
    if not isinstance(endpoint, str) or not endpoint:
        raise WorkerError("LOCAL_ENDPOINT_INVALID")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise WorkerError("LOCAL_ENDPOINT_INVALID") from exc
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
        raise WorkerError("LOCAL_ENDPOINT_NOT_EXACT_LOOPBACK")
    return endpoint


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != REQUEST_FIELDS:
        raise WorkerError("WORKER_REQUEST_FIELDS_INVALID")
    accepted = dict(value)
    if accepted["schema_version"] != SCHEMA_VERSION:
        raise WorkerError("WORKER_REQUEST_SCHEMA_VERSION_INVALID")
    accepted["endpoint"] = _validate_endpoint(accepted["endpoint"])
    for key in ("model", "expected_canonical_model", "normalization", "behavior_hash"):
        if not isinstance(accepted[key], str) or not accepted[key]:
            raise WorkerError("WORKER_REQUEST_VALUE_INVALID")
    digest = accepted["model_manifest_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789ABCDEF" for character in digest)
    ):
        raise WorkerError("MODEL_MANIFEST_DIGEST_INVALID")
    if accepted["normalization"] != "L2_UNIT":
        raise WorkerError("NORMALIZATION_POLICY_INVALID")
    for key in ("expected_dimension", "max_batch_size", "max_input_utf8_bytes"):
        item = accepted[key]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise WorkerError("WORKER_REQUEST_LIMIT_INVALID")
    if accepted["max_batch_size"] > 4:
        raise WorkerError("WORKER_BATCH_LIMIT_EXCEEDS_AUTHORIZATION")
    for key in ("normalization_min", "normalization_max", "timeout_seconds"):
        item = accepted[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise WorkerError("WORKER_REQUEST_LIMIT_INVALID")
    if (
        accepted["normalization_min"] <= 0
        or accepted["normalization_max"] < accepted["normalization_min"]
        or accepted["timeout_seconds"] <= 0
    ):
        raise WorkerError("WORKER_REQUEST_LIMIT_INVALID")
    texts = accepted["texts"]
    if (
        not isinstance(texts, list)
        or not texts
        or len(texts) > accepted["max_batch_size"]
        or any(not isinstance(item, str) or not item for item in texts)
    ):
        raise WorkerError("WORKER_TEXT_FIXTURE_INVALID")
    if sum(len(item.encode("utf-8")) for item in texts) > accepted["max_input_utf8_bytes"]:
        raise WorkerError("WORKER_INPUT_BYTES_EXCEEDED")
    if _canonical_model(accepted["model"]) != accepted["expected_canonical_model"]:
        raise WorkerError("REQUESTED_MODEL_CANONICAL_MISMATCH")
    return accepted


def _validate_vectors(request: Mapping[str, Any], returned_model: Any, vectors: Any) -> tuple[str, list[list[float]]]:
    if not isinstance(returned_model, str) or not returned_model:
        raise WorkerError("RETURNED_MODEL_MISSING")
    if _canonical_model(returned_model) != request["expected_canonical_model"]:
        raise WorkerError("RETURNED_MODEL_IDENTITY_MISMATCH")
    if not isinstance(vectors, list) or len(vectors) != len(request["texts"]):
        raise WorkerError("EMBEDDING_VECTOR_COUNT_MISMATCH")
    accepted: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != request["expected_dimension"]:
            raise WorkerError("EMBEDDING_VECTOR_DIMENSION_MISMATCH")
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in vector
        ):
            raise WorkerError("EMBEDDING_VECTOR_NONFINITE")
        normalized = [float(item) for item in vector]
        norm = math.sqrt(sum(item * item for item in normalized))
        if not request["normalization_min"] <= norm <= request["normalization_max"]:
            raise WorkerError("EMBEDDING_VECTOR_NORMALIZATION_MISMATCH")
        accepted.append(normalized)
    return returned_model, accepted


def _result(
    request: Mapping[str, Any] | None,
    *,
    status: str,
    request_started: bool,
    returned_model: str | None,
    embeddings: list[list[float]],
    output_hash: str | None,
    latency_ms: float,
    error_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_VERSION,
        "worker_version": WORKER_VERSION,
        "status": status,
        "request_started": request_started,
        "requested_model": request.get("model") if request else None,
        "returned_model": returned_model,
        "model_manifest_sha256": request.get("model_manifest_sha256") if request else None,
        "embeddings": embeddings,
        "vector_count": len(embeddings),
        "dimension": request.get("expected_dimension") if request and embeddings else None,
        "normalization": request.get("normalization") if request and embeddings else None,
        "input_hash": _sha256(request.get("texts")) if request else None,
        "output_hash": output_hash,
        "latency_ms": round(latency_ms, 3),
        "error_code": error_code,
    }


def execute(request: Mapping[str, Any], prestart_path: Path, terminal_path: Path) -> dict[str, Any]:
    accepted = validate_request(request)
    body = _canonical_bytes({"model": accepted["model"], "input": accepted["texts"]})
    prestart = {
        "schema_version": "LOCAL_RETRIEVAL_LOCAL_MODEL_PRESTART_V1",
        "started_at": _utc_now(),
        "request_started": True,
        "route": "loopback_http",
        "endpoint_host": urlsplit(accepted["endpoint"]).hostname,
        "requested_model": accepted["model"],
        "model_manifest_sha256": accepted["model_manifest_sha256"],
        "behavior_hash": accepted["behavior_hash"],
        "input_hash": _sha256(accepted["texts"]),
        "request_body_sha256": hashlib.sha256(body).hexdigest().upper(),
    }
    _write_create_only(prestart_path, prestart)
    started = time.monotonic()
    returned_model: str | None = None
    output_hash: str | None = None
    error_code: str | None = None
    vectors: list[list[float]] = []
    status = "error"
    try:
        http_request = urllib.request.Request(
            accepted["endpoint"], data=body, method="POST"
        )
        http_request.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(http_request, timeout=float(accepted["timeout_seconds"])) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                raise WorkerError("LOCAL_RESPONSE_BYTES_EXCEEDED")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WorkerError("LOCAL_RESPONSE_BYTES_EXCEEDED")
        decoded = json.loads(raw.decode("utf-8", errors="strict"))
        if not isinstance(decoded, Mapping):
            raise WorkerError("LOCAL_RESPONSE_NOT_OBJECT")
        returned_model, vectors = _validate_vectors(
            accepted, decoded.get("model"), decoded.get("embeddings")
        )
        output_hash = _sha256(vectors)
        status = "ok"
    except WorkerError as exc:
        error_code = exc.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        error_code = "LOCAL_EMBEDDING_REQUEST_FAILED"
    latency_ms = (time.monotonic() - started) * 1000
    result = _result(
        accepted,
        status=status,
        request_started=True,
        returned_model=returned_model,
        embeddings=vectors,
        output_hash=output_hash,
        latency_ms=latency_ms,
        error_code=error_code,
    )
    terminal = {
        "schema_version": "LOCAL_RETRIEVAL_LOCAL_MODEL_TERMINAL_V1",
        "finished_at": _utc_now(),
        "status": status,
        "request_started": True,
        "requested_model": accepted["model"],
        "returned_model": returned_model,
        "model_manifest_sha256": accepted["model_manifest_sha256"],
        "behavior_hash": accepted["behavior_hash"],
        "input_hash": _sha256(accepted["texts"]),
        "output_hash": output_hash or _sha256({"error_code": error_code}),
        "latency_ms": round(latency_ms, 3),
        "dimension": accepted["expected_dimension"] if vectors else None,
        "normalization": accepted["normalization"] if vectors else None,
        "error_code": error_code,
    }
    _write_create_only(terminal_path, terminal)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--prestart", required=True)
    parser.add_argument("--terminal", required=True)
    args = parser.parse_args()
    request: Mapping[str, Any] | None = None
    started = time.monotonic()
    try:
        decoded = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if isinstance(decoded, Mapping):
            request = decoded
        else:
            raise WorkerError("WORKER_REQUEST_NOT_OBJECT")
        result = execute(request, Path(args.prestart), Path(args.terminal))
    except WorkerError as exc:
        result = _result(
            request,
            status="error",
            request_started=False,
            returned_model=None,
            embeddings=[],
            output_hash=None,
            latency_ms=(time.monotonic() - started) * 1000,
            error_code=exc.code,
        )
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        result = _result(
            request,
            status="error",
            request_started=False,
            returned_model=None,
            embeddings=[],
            output_hash=None,
            latency_ms=(time.monotonic() - started) * 1000,
            error_code="WORKER_INPUT_READ_FAILED",
        )
    print(_canonical_bytes(result).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
