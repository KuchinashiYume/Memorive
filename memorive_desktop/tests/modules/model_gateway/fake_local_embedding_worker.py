"""Offline-only fake worker for the LOCAL-RETRIEVAL local embedding seam tests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


RESULT_VERSION = "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_RESULT_V1"
WORKER_VERSION = "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_V1"


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


def _write_create_only(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_canonical_bytes(value))


def _unit_vector(index: int, dimension: int) -> list[float]:
    vector = [0.0] * dimension
    vector[index % dimension] = 1.0
    return vector


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--prestart", required=True)
    parser.add_argument("--terminal", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    texts = list(request["texts"])
    sentinel = texts[0]
    input_hash = _sha256(texts)

    if sentinel == "__FAKE_EMBEDDING_NO_MODEL_START__":
        result = {
            "schema_version": RESULT_VERSION,
            "worker_version": WORKER_VERSION,
            "status": "error",
            "request_started": False,
            "requested_model": request["model"],
            "returned_model": None,
            "model_manifest_sha256": request["model_manifest_sha256"],
            "embeddings": [],
            "vector_count": 0,
            "dimension": None,
            "normalization": None,
            "input_hash": input_hash,
            "output_hash": None,
            "latency_ms": 0.0,
            "error_code": "FAKE_NO_MODEL_START",
        }
        print(_canonical_bytes(result).decode("utf-8"), flush=True)
        return 0

    prestart = {
        "schema_version": "LOCAL_RETRIEVAL_LOCAL_MODEL_PRESTART_V1",
        "started_at": "2026-08-14T14:00:00Z",
        "request_started": True,
        "route": "loopback_http",
        "endpoint_host": "127.0.0.1",
        "requested_model": request["model"],
        "model_manifest_sha256": request["model_manifest_sha256"],
        "behavior_hash": request["behavior_hash"],
        "input_hash": input_hash,
        "request_body_sha256": _sha256({"model": request["model"], "input": texts}),
    }
    _write_create_only(Path(args.prestart), prestart)

    dimension = int(request["expected_dimension"])
    if sentinel == "__FAKE_EMBEDDING_WRONG_DIMENSION__":
        dimension = max(1, dimension - 1)
    vectors = [_unit_vector(index, dimension) for index, _ in enumerate(texts)]
    returned_model = request["model"]
    if sentinel == "__FAKE_EMBEDDING_RETURNED_MODEL_MISMATCH__":
        returned_model = "different-model:latest"
    output_hash = _sha256(vectors)
    behavior_hash = request["behavior_hash"]
    if sentinel == "__FAKE_EMBEDDING_TERMINAL_BINDING_MISMATCH__":
        behavior_hash = "F" * 64

    terminal = {
        "schema_version": "LOCAL_RETRIEVAL_LOCAL_MODEL_TERMINAL_V1",
        "finished_at": "2026-08-14T14:00:01Z",
        "status": "ok",
        "request_started": True,
        "requested_model": request["model"],
        "returned_model": returned_model,
        "model_manifest_sha256": request["model_manifest_sha256"],
        "behavior_hash": behavior_hash,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "latency_ms": 1.0,
        "dimension": dimension,
        "normalization": request["normalization"],
        "error_code": None,
    }
    _write_create_only(Path(args.terminal), terminal)
    result = {
        "schema_version": RESULT_VERSION,
        "worker_version": WORKER_VERSION,
        "status": "ok",
        "request_started": True,
        "requested_model": request["model"],
        "returned_model": returned_model,
        "model_manifest_sha256": request["model_manifest_sha256"],
        "embeddings": vectors,
        "vector_count": len(vectors),
        "dimension": dimension,
        "normalization": request["normalization"],
        "input_hash": input_hash,
        "output_hash": output_hash,
        "latency_ms": 1.0,
        "error_code": None,
    }
    print(_canonical_bytes(result).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
