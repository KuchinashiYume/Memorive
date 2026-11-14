"""Read-only asset/runtime/license doctor for the Installation local reranker."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_sha256, file_sha256, validate_compatibility_manifest


def _check_file(path: Path, expected_bytes: int | None, expected_sha256: str) -> dict[str, Any]:
    exists = path.is_file()
    actual_bytes = path.stat().st_size if exists else None
    actual_sha256 = file_sha256(path) if exists and actual_bytes == expected_bytes else None
    return {
        "path": str(path),
        "exists": exists,
        "expected_bytes": expected_bytes,
        "actual_bytes": actual_bytes,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "hash_match": exists and actual_bytes == expected_bytes and actual_sha256 == expected_sha256,
    }


def reranker_doctor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify every frozen member without loading or starting the model."""

    manifest = validate_compatibility_manifest(value)
    model_root = Path(manifest["model_root"])
    model_checks = [
        _check_file(model_root / member["path"], member["bytes"], member["sha256"])
        for member in manifest["model_file_verification"]["members"]
    ]
    model_sums = manifest["model_file_verification"]["sha256sums"]
    model_sums_check = _check_file(
        Path(model_sums["locator"]), model_sums.get("bytes"), manifest["model_sha256sums_sha256"]
    )
    runtime = manifest["runtime"]
    executable = Path(runtime["python_executable"])
    executable_check = _check_file(
        executable,
        executable.stat().st_size if executable.is_file() else None,
        runtime["python_executable_sha256"],
    )
    runtime_root = Path(runtime["runtime_freeze_root"])
    runtime_checks = [
        _check_file(runtime_root / member["path"], member["bytes"], member["sha256"])
        for member in runtime["runtime_file_verification"]["members"]
    ]
    runtime_sums = runtime["runtime_file_verification"]["sha256sums"]
    runtime_sums_check = _check_file(
        Path(runtime_sums["locator"]), runtime_sums.get("bytes"), runtime["runtime_sha256sums_sha256"]
    )
    license_binding = manifest["license"]
    license_path = Path(license_binding["license_file"])
    license_check = _check_file(
        license_path,
        license_path.stat().st_size if license_path.is_file() else None,
        license_binding["license_file_sha256"],
    )
    checks = model_checks + runtime_checks + [
        model_sums_check,
        runtime_sums_check,
        executable_check,
        license_check,
    ]
    receipt = {
        "schema_version": "RetrievalCalibrationLocalRerankerDoctorReceipt-v1",
        "model_id": manifest["model_id"],
        "canonical_revision": manifest["canonical_revision"],
        "manifest_sha256": canonical_sha256(manifest),
        "model_member_checks": model_checks,
        "model_sha256sums_check": model_sums_check,
        "runtime_member_checks": runtime_checks,
        "runtime_sha256sums_check": runtime_sums_check,
        "runtime_executable_check": executable_check,
        "license_check": license_check,
        "device_binding": manifest["device"],
        "device_live_probe": "NOT_RUN_DOCTOR_DOES_NOT_START_MODEL",
        "network_requests": 0,
        "external_requests": 0,
        "physical_model_starts": 0,
        "cloud_fallback": False,
        "verdict": "PASS" if all(check["hash_match"] for check in checks) else "FAIL",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt
