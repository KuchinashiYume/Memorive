"""Memorive ANALYSIS-ADMISSION/ARTIFACT_REGISTRY sandbox-only Artifact Envelope v2 materialization."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import SandboxBoundaryViolation
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
        if not _contains(canonical_sandbox, root):
            raise SandboxBoundaryViolation("sandbox_root must be under G:\\Memorive-沙盒")
        object.__setattr__(self, "sandbox_root", root)
        object.__setattr__(self, "protected_roots", protected)

    @classmethod
    def build(
        cls,
        sandbox_root: Path | str,
        protected_roots: Iterable[Path | str],
    ) -> "SandboxBoundary":
        root = Path(sandbox_root).resolve(strict=False)
        supplied = tuple(
            Path(item).resolve(strict=False) for item in protected_roots
        )
        return cls(
            sandbox_root=root,
            protected_roots=supplied,
        )
