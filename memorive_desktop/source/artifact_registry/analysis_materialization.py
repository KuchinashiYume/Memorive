"""Memorive ANALYSIS-ADMISSION/ARTIFACT_REGISTRY sandbox-only Artifact Envelope v2 materialization."""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ContractValidationError, SandboxBoundaryViolation
from .registry_v2 import ArtifactRegistry
from .schema_v2 import build_envelope, canonical_json, parent_link, sha256_file


_SAFE_NAME = re.compile(r"[^a-z0-9_-]+")
_CANONICAL_SANDBOX_ROOT = Path(r"G:\Memorive-沙盒")
_CANONICAL_PROTECTED_ROOTS = (
    Path(r"G:\Memorive"),
    Path(r"G:\Memorive-运维"),
    Path(r"G:\Memorive-ops"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _intersects(left: Path, right: Path) -> bool:
    return _contains(left, right) or _contains(right, left)


@dataclass(frozen=True)
class SandboxBoundary:
    """Closed allow-list for every Configuration write target."""

    sandbox_root: Path
    protected_roots: tuple[Path, ...]
    _allow_test_temp_root: bool = False

    def __post_init__(self) -> None:
        root = Path(self.sandbox_root).resolve(strict=False)
        mandatory = tuple(
            item.resolve(strict=False) for item in _CANONICAL_PROTECTED_ROOTS
        )
        supplied = tuple(
            Path(item).resolve(strict=False) for item in self.protected_roots
        )
        protected = tuple(dict.fromkeys((*mandatory, *supplied)))
        if any(_intersects(item, root) for item in protected):
            raise SandboxBoundaryViolation(
                "sandbox_root must be disjoint from every protected root"
            )
        canonical_sandbox = _CANONICAL_SANDBOX_ROOT.resolve(strict=False)
        test_temp = Path(tempfile.gettempdir()).resolve(strict=False)
        canonical_root = _contains(canonical_sandbox, root)
        authorized_test_root = self._allow_test_temp_root and _contains(
            test_temp, root
        )
        if not canonical_root and not authorized_test_root:
            raise SandboxBoundaryViolation(
                "sandbox_root must be under G:\\Memorive-沙盒; only an explicit "
                "test-only temporary root is permitted outside it"
            )
        object.__setattr__(self, "sandbox_root", root)
        object.__setattr__(self, "protected_roots", protected)

    @classmethod
    def build(
        cls,
        sandbox_root: Path | str,
        protected_roots: Iterable[Path | str],
        *,
        allow_test_temp_root: bool = False,
    ) -> "SandboxBoundary":
        root = Path(sandbox_root).resolve(strict=False)
        supplied = tuple(
            Path(item).resolve(strict=False) for item in protected_roots
        )
        return cls(
            sandbox_root=root,
            protected_roots=supplied,
            _allow_test_temp_root=allow_test_temp_root,
        )

    def require_write_path(self, path: Path | str) -> Path:
        target = Path(path).resolve(strict=False)
        if not _contains(self.sandbox_root, target):
            raise SandboxBoundaryViolation(
                f"write target is outside Configuration sandbox: {target}"
            )
        for protected in self.protected_roots:
            if _contains(protected, target) or target == protected:
                raise SandboxBoundaryViolation(
                    f"write target intersects protected root: {target}"
                )
        return target
