from __future__ import annotations

from pathlib import Path
import os
from typing import Any, Mapping, Sequence

from memorive_folder_management.policy import windows_io_path


class DirectoryPolicy:
    def __init__(
        self,
        allowed_roots: Sequence[Path | str],
        *,
        minimum_free_bytes: int = 10 * 1024 * 1024,
        probe_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self.allowed_roots = tuple(
            Path(root).resolve(strict=False) for root in allowed_roots
        )
        if not self.allowed_roots:
            raise ValueError("DIRECTORY_ALLOWLIST_EMPTY")
        self.minimum_free_bytes = minimum_free_bytes
        self.probe_overrides = {
            str(Path(key).resolve(strict=False)): dict(value)
            for key, value in (probe_overrides or {}).items()
        }

    def _inside_allowlist(self, target: Path) -> bool:
        for root in self.allowed_roots:
            try:
                target.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def register_user_selected_root(self, target: Path | str) -> Path:
        raw = Path(target)
        if not raw.is_absolute() or ".." in raw.parts:
            raise ValueError("DIRECTORY_SELECTION_INVALID")
        resolved = raw.resolve(strict=False)
        resolved_io = windows_io_path(resolved)
        if not resolved_io.is_dir() or raw.is_symlink() or resolved_io.is_symlink():
            raise ValueError("DIRECTORY_SELECTION_NOT_REGULAR_DIRECTORY")
        if resolved not in self.allowed_roots:
            self.allowed_roots = (*self.allowed_roots, resolved)
        return resolved

    def _probe(self, target: Path) -> dict[str, Any]:
        override = self.probe_overrides.get(str(target))
        if override is not None:
            return {
                "exists": bool(override.get("exists", True)),
                "writable": bool(override.get("writable", True)),
                "free_bytes": int(
                    override.get("free_bytes", self.minimum_free_bytes * 2)
                ),
            }
        target_io = windows_io_path(target)
        return {
            "exists": target_io.exists(),
            "writable": target_io.exists() and target_io.is_dir() and os.access(target_io, os.W_OK),
            "free_bytes": self.minimum_free_bytes * 2,
        }

    def inspect(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        directories = settings["directories"]
        workspace = Path(directories["workspace_root"]).resolve(strict=False)
        artifacts = Path(directories["artifact_root"]).resolve(strict=False)
        errors = []
        if workspace == artifacts:
            errors.append("DIRECTORY_ROLE_CONFLICT")
        rows = []
        targets = [
            ("workspace_root", workspace),
            ("artifact_root", artifacts),
        ]
        external = directories["external_library"]
        if external["enabled"]:
            targets.append(
                ("external_library", Path(external["root"]).resolve(strict=False))
            )
        for role, target in targets:
            allowed = self._inside_allowlist(target)
            probe = self._probe(target)
            reason = "NONE"
            if not allowed:
                reason = "DIRECTORY_OUTSIDE_ALLOWLIST"
            elif not probe["exists"]:
                reason = "DIRECTORY_MISSING"
            elif not probe["writable"]:
                reason = "DIRECTORY_NOT_WRITABLE"
            elif probe["free_bytes"] < self.minimum_free_bytes:
                reason = "DISK_SPACE_INSUFFICIENT"
            if reason != "NONE":
                errors.append(reason)
            rows.append(
                {
                    "role": role,
                    "path": str(target),
                    "allowed": allowed,
                    "exists": probe["exists"],
                    "writable": probe["writable"],
                    "free_bytes": probe["free_bytes"],
                    "reason": reason,
                }
            )
        return {
            "schema_version": "DirectoryRolePreflight-v1",
            "roles": rows,
            "errors": sorted(set(errors)),
            "status": "PASS" if not errors else "BLOCKED",
            "mutations": 0,
        }


__all__ = ["DirectoryPolicy"]
