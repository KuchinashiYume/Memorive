"""Runtime/model identity checks that never start or contact a runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import file_sha256, validate_contract
from .errors import IdentityBlocked
from .types import FileIdentity, LocalRuntimeAssetManifest


def _verify_file(binding: FileIdentity, *, allowed_roots: tuple[str, ...]) -> dict[str, Any]:
    path = Path(binding.locator)
    if not path.is_file():
        raise IdentityBlocked("ASSET_FILE_MISSING", details={"locator": binding.locator})
    resolved = path.resolve()
    roots = tuple(Path(root).resolve() for root in allowed_roots)
    if roots and not any(resolved == root or root in resolved.parents for root in roots):
        raise IdentityBlocked("ASSET_OUTSIDE_ALLOWED_ROOT", details={"locator": str(resolved)})
    actual_bytes = path.stat().st_size
    actual_sha = file_sha256(path)
    if actual_bytes != binding.bytes or actual_sha != binding.sha256:
        raise IdentityBlocked(
            "ASSET_IDENTITY_DRIFT",
            details={
                "locator": binding.locator,
                "expected_sha256": binding.sha256,
                "actual_sha256": actual_sha,
                "expected_bytes": binding.bytes,
                "actual_bytes": actual_bytes,
            },
        )
    return {"locator": str(resolved), "sha256": actual_sha, "bytes": actual_bytes, "result": "MATCH"}


def verify_runtime_manifest(
    manifest: LocalRuntimeAssetManifest,
    *,
    allowed_runtime_roots: tuple[str, ...],
    allowed_model_roots: tuple[str, ...],
) -> dict[str, Any]:
    """Verify all asset identities without reading payloads or launching a process."""

    validate_contract(manifest)
    runtime = _verify_file(manifest.executable, allowed_roots=allowed_runtime_roots)
    model_rows = {
        "manifest": _verify_file(manifest.model_manifest, allowed_roots=allowed_model_roots),
        "blob": _verify_file(manifest.model_blob, allowed_roots=allowed_model_roots),
        "license": _verify_file(manifest.license_file, allowed_roots=allowed_model_roots),
    }
    if manifest.projector is not None:
        model_rows["projector"] = _verify_file(manifest.projector, allowed_roots=allowed_model_roots)
    return {
        "manifest_id": manifest.manifest_id,
        "runtime": runtime,
        "model_files": model_rows,
        "model_process_started": False,
        "model_request_count": 0,
        "result": "PASS_IDENTITY_ONLY",
    }
