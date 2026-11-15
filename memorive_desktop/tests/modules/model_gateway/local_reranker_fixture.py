"""A synthetic local reranker installation (model, licence, runtime) for the reranker tests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

from model_gateway.local_reranker.contracts import file_sha256


def provenance(*, source_role: str = "results", page_family: str = "digital") -> dict:
    value = {
        "schema_version": "RetrievalCalibrationRerankerDocumentProvenance-v1",
        "retrieval_terminal_sha256": "A" * 64,
        "page_family": page_family,
        "page_family_modifiers": [],
        "page_span": {"start": 1, "end": 1},
        "source_role": source_role,
        "metadata_status": "vector",
        "field_provenance": {"page_family": "vector", "source_role": "vector"},
    }
    from model_gateway.local_reranker.contracts import canonical_sha256

    value["provenance_sha256"] = canonical_sha256(value)
    return value


def _write(path: Path, value: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return {"path": path.name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest().upper()}


def make_fake_manifest(root: Path) -> dict:
    model_root = root / "model"
    runtime_root = root / "runtime"
    member = _write(model_root / "dummy.bin", b"model")
    license_path = model_root / "LICENSE"
    license_path.write_bytes(b"Apache-2.0")
    license_hash = file_sha256(license_path)
    model_sums = model_root / "MODEL_SHA256SUMS"
    model_sums.write_text(f"{member['sha256']}  dummy.bin\n", encoding="utf-8")
    runtime_member = _write(runtime_root / "requirements.freeze.txt", b"fake==1\n")
    runtime_sums = runtime_root / "RUNTIME_SHA256SUMS"
    runtime_sums.write_text(f"{runtime_member['sha256']}  requirements.freeze.txt\n", encoding="utf-8")
    return {
        "schema_version": "RetrievalCalibrationLocalRerankerCompatibilityManifest-v1",
        "model_id": "local/fake-reranker",
        "canonical_revision": "fake001",
        "model_root": str(model_root),
        "model_sha256sums_sha256": file_sha256(model_sums),
        "weights_exact_set_sha256": "A" * 64,
        "model_file_verification": {
            "members": [member],
            "sha256sums": {"locator": str(model_sums), "bytes": model_sums.stat().st_size},
        },
        "license": {
            "identifier": "Apache-2.0",
            "license_file": str(license_path),
            "license_file_sha256": license_hash,
            "local_evaluation_authorized": True,
            "redistribution_in_this_task": False,
        },
        "runtime": {
            "python_executable": sys.executable,
            "python_executable_sha256": file_sha256(sys.executable),
            "dependency_lock_sha256": runtime_member["sha256"],
            "runtime_sha256sums_sha256": file_sha256(runtime_sums),
            "runtime_freeze_root": str(runtime_root),
            "runtime_file_verification": {
                "members": [runtime_member],
                "sha256sums": {"locator": str(runtime_sums), "bytes": runtime_sums.stat().st_size},
            },
            "packages": {"fake": "1"},
            "offline_environment": {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                "TOKENIZERS_PARALLELISM": "false",
            },
        },
        "device": {"kind": "cpu", "index": 0, "dtype": "float32", "quantization": "none", "total_memory_bytes": 1},
        "input_contract": {
            "instruction": "Retrieve passages that directly answer the query.",
            "batch_size": 4,
            "max_documents_per_query": 32,
            "max_input_tokens": 2048,
            "truncation": "longest_first",
            "candidate_order_must_match_snapshot": True,
        },
        "score_contract": {
            "direction": "higher_is_more_relevant",
            "range": [0.0, 1.0],
            "finite_required": True,
            "index_alignment": "EXACT_UNIQUE_ZERO_BASED",
            "partial_batch_acceptance": False,
            "document_mutation_allowed": False,
        },
        "physical_starts_authorized": {"construction_a_max": 4, "validation_b_exact": 1, "retry_inside_b": 0, "task_total_max": 5},
        "network_allowlist": [],
        "cloud_fallback": False,
        "qualification_offline_required": True,
    }
