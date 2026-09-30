from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    base_object,
    bytes_sha256,
    immutable_copy,
    utc_now,
    validate_initialization_object,
)


SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkspaceViolation(ValueError):
    def __init__(self, code: str, path: str | Path | None = None):
        self.code = code
        self.path = str(path) if path is not None else None
        suffix = f": {self.path}" if self.path else ""
        super().__init__(f"{code}{suffix}")


def _normal(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _is_within(path: Path, root: Path) -> bool:
    path_norm = _normal(path)
    root_norm = _normal(root)
    try:
        return os.path.commonpath([path_norm, root_norm]) == root_norm
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _has_reparse_component(path: Path) -> bool:
    current = Path(os.path.abspath(str(path)))
    while True:
        if current.exists() and _is_reparse_point(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


class JobWorkspace:
    """Create-only job workspace with explicit read/write/process boundaries."""

    def __init__(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        core_schema: Mapping[str, Any],
    ):
        self.root = root
        self.input_root = root / "input"
        self.output_root = root / "output"
        self.receipt_root = root / "receipt"
        self.scratch_root = root / "scratch"
        self.manifest = immutable_copy(manifest)
        self._core_schema = immutable_copy(core_schema)
        self._read_roots = tuple(Path(value) for value in self.manifest["read_allowlist"])
        self._write_roots = tuple(Path(value) for value in self.manifest["write_allowlist"])

    @classmethod
    def create(
        cls,
        parent: str | Path,
        job_id: str,
        core_schema: Mapping[str, Any],
        *,
        process_allowlist: Sequence[str],
        environment_allowlist: Sequence[str],
        extra_read_roots: Sequence[str | Path] = (),
        protected_roots: Sequence[str | Path] = (),
        network_allowlist: Sequence[str] = (),
        input_hashes: Sequence[Mapping[str, Any]] = (),
        selected_adapter_id: str | None = None,
        selected_manifest_hash: str | None = None,
        expected_attempt_id: str | None = None,
        contract_timeout_seconds: float | None = None,
        raw_cap_bytes: int | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> "JobWorkspace":
        if not SAFE_JOB_ID.fullmatch(job_id):
            raise WorkspaceViolation("JOB_ID_INVALID", job_id)
        if network_allowlist:
            raise WorkspaceViolation("NETWORK_POLICY_VIOLATION")
        supplied_parent = Path(parent)
        if _has_reparse_component(supplied_parent):
            raise WorkspaceViolation("WORKSPACE_PARENT_INVALID", supplied_parent)
        parent_path = supplied_parent.resolve(strict=True)
        if not parent_path.is_dir():
            raise WorkspaceViolation("WORKSPACE_PARENT_INVALID", parent_path)
        root = parent_path / job_id
        for protected in protected_roots:
            protected_path = Path(protected).resolve(strict=False)
            if _is_within(root, protected_path) or _is_within(protected_path, root):
                raise WorkspaceViolation("PROTECTED_ROOT_OVERLAP", root)
        try:
            root.mkdir(exist_ok=False)
            for name in ("input", "output", "receipt", "scratch"):
                (root / name).mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise WorkspaceViolation("WORKSPACE_CREATE_ONLY_COLLISION", root) from exc
        except OSError as exc:
            raise WorkspaceViolation("WORKSPACE_CREATE_FAILED", root) from exc

        resolved_root = root.resolve(strict=True)
        if _is_reparse_point(resolved_root):
            raise WorkspaceViolation("WORKSPACE_SYMLINK_FORBIDDEN", resolved_root)
        input_root = resolved_root / "input"
        output_root = resolved_root / "output"
        receipt_root = resolved_root / "receipt"
        scratch_root = resolved_root / "scratch"
        read_roots = [input_root]
        for candidate in extra_read_roots:
            supplied = Path(candidate)
            if _has_reparse_component(supplied):
                raise WorkspaceViolation("READ_ROOT_SYMLINK_FORBIDDEN", supplied)
            resolved = supplied.resolve(strict=True)
            read_roots.append(resolved)
        write_roots = [output_root, receipt_root, scratch_root]

        manifest = base_object(
            "JobWorkspaceManifest",
            f"jobworkspacemanifest_{job_id}",
            producer="Execution_JOB_RUNNER",
            single_writer="Execution_JOB_RUNNER",
            consumers=("Execution_JOB_RUNNER", "RUNTIME_LOG"),
            clock=clock,
            extensions={
                "selected_adapter_id": selected_adapter_id,
                "selected_manifest_hash": selected_manifest_hash,
                "expected_attempt_id": expected_attempt_id,
                "contract_timeout_seconds": contract_timeout_seconds,
                "raw_cap_bytes": raw_cap_bytes,
            },
        )
        manifest.update(
            {
                "job_id": job_id,
                "workspace_root": str(resolved_root),
                "input_root": str(input_root),
                "output_root": str(output_root),
                "receipt_root": str(receipt_root),
                "scratch_root": str(scratch_root),
                "read_allowlist": [str(path) for path in read_roots],
                "write_allowlist": [str(path) for path in write_roots],
                "network_allowlist": [],
                "process_allowlist": list(dict.fromkeys(process_allowlist)),
                "environment_allowlist": sorted(set(environment_allowlist)),
                "cleanup_policy": {"automatic_cleanup": False, "owner": "AUTHORIZED_CLEANUP"},
                "retention_policy": {"mode": "PRESERVE_UNTIL_EXPLICIT_DISPOSITION"},
                "input_hashes": [immutable_copy(value) for value in input_hashes],
            }
        )
        accepted = validate_initialization_object(manifest, "JobWorkspaceManifest", core_schema)
        return cls(resolved_root, accepted, core_schema)

    def assert_cwd(self, path: str | Path) -> Path:
        supplied = Path(path)
        if _has_reparse_component(supplied):
            raise WorkspaceViolation("WORKING_DIRECTORY_POLICY_VIOLATION", supplied)
        candidate = supplied.resolve(strict=True)
        if not candidate.is_dir() or not _is_within(candidate, self.root):
            raise WorkspaceViolation("WORKING_DIRECTORY_POLICY_VIOLATION", candidate)
        return candidate

    def assert_read_path(self, path: str | Path) -> Path:
        supplied = Path(path)
        if _has_reparse_component(supplied):
            raise WorkspaceViolation("READ_PATH_POLICY_VIOLATION", supplied)
        candidate = supplied.resolve(strict=True)
        if not any(_is_within(candidate, root) for root in self._read_roots):
            raise WorkspaceViolation("READ_PATH_POLICY_VIOLATION", candidate)
        return candidate

    def assert_write_path(self, path: str | Path) -> Path:
        supplied = Path(path)
        if _has_reparse_component(supplied):
            raise WorkspaceViolation("WRITE_PATH_POLICY_VIOLATION", supplied)
        candidate = supplied.resolve(strict=False)
        if not any(_is_within(candidate, root) for root in self._write_roots):
            raise WorkspaceViolation("WRITE_PATH_POLICY_VIOLATION", candidate)
        return candidate

    def stage_input(self, relative_path: str, data: bytes) -> dict[str, Any]:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise WorkspaceViolation("INPUT_RELATIVE_PATH_INVALID", relative_path)
        target = self.input_root / relative
        if not _is_within(target, self.input_root):
            raise WorkspaceViolation("INPUT_PATH_ESCAPE", target)
        cursor = self.input_root
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.exists() and _is_reparse_point(cursor):
                raise WorkspaceViolation("INPUT_REPARSE_POINT_FORBIDDEN", cursor)
        declarations = {
            entry.get("relative_path"): entry
            for entry in self.manifest["input_hashes"]
            if isinstance(entry, Mapping)
            and isinstance(entry.get("relative_path"), str)
        }
        declaration = declarations.get(relative.as_posix())
        if declaration is None:
            raise WorkspaceViolation("INPUT_NOT_DECLARED", relative_path)
        actual_sha256 = bytes_sha256(data)
        if declaration.get("sha256") != actual_sha256 or declaration.get("bytes") != len(
            data
        ):
            raise WorkspaceViolation("INPUT_HASH_MISMATCH", relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as handle:
                handle.write(data)
        except FileExistsError as exc:
            raise WorkspaceViolation("INPUT_CREATE_ONLY_COLLISION", target) from exc
        return {
            "path": str(target),
            "bytes": len(data),
            "sha256": actual_sha256,
        }
