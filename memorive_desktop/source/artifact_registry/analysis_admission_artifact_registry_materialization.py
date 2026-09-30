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
from .snapshots import (
    build_compatibility_policy_snapshot_envelope,
    build_registry_snapshot_envelope,
)


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


class ArtifactFactory:
    """Create and register immutable v2 artifacts inside one scenario root."""

    def __init__(
        self,
        *,
        boundary: SandboxBoundary,
        scenario_root: Path | str,
        run_ref: str,
        registered_at: str,
    ) -> None:
        self.boundary = boundary
        self.scenario_root = boundary.require_write_path(scenario_root)
        self.payload_root = boundary.require_write_path(
            self.scenario_root / "artifact_payloads"
        )
        self.registry_root = boundary.require_write_path(
            self.scenario_root / "artifact_registry"
        )
        self.corruption_root = boundary.require_write_path(
            self.scenario_root / "registry_corruption_evidence"
        )
        self.run_ref = run_ref
        self.registered_at = registered_at
        self.registry = ArtifactRegistry(
            self.registry_root,
            corruption_evidence_root=self.corruption_root,
            clock=lambda: self.registered_at,
        )

    def create(
        self,
        *,
        artifact_type: str,
        payload: Mapping[str, Any],
        schema_ref: str,
        paper_ids: Iterable[str] = (),
        source_artifact_ids: Iterable[str] = (),
        parents: Iterable[Mapping[str, Any]] = (),
        unresolved: Iterable[Mapping[str, Any]] = (),
        conflicts: Iterable[Mapping[str, Any]] = (),
        metadata: Mapping[str, Any] | None = None,
        evidence_refs: Iterable[str],
        legacy_import: bool = False,
        semantic_key: str = "default",
    ) -> dict[str, Any]:
        if not artifact_type.strip() or not schema_ref.strip():
            raise ContractValidationError("artifact_type and schema_ref are required")
        encoded = (canonical_json(dict(payload)) + "\n").encode("utf-8")
        content_hash = sha256_bytes(encoded)
        seed = "|".join((self.run_ref, artifact_type, semantic_key, content_hash))
        artifact_id = "art_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        safe_type = _SAFE_NAME.sub("_", artifact_type.lower()).strip("_")
        payload_path = self.boundary.require_write_path(
            self.payload_root / f"{artifact_id}_{safe_type}.json"
        )
        self._write_once(payload_path, encoded)
        envelope = build_envelope(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            schema_ref=schema_ref,
            content_hash=content_hash,
            locator_path=payload_path,
            registered_at=self.registered_at,
            run_ref=self.run_ref,
            paper_ids=tuple(paper_ids),
            source_artifact_ids=tuple(source_artifact_ids),
            parent_artifacts=tuple(parents),
            unresolved_parent_requirements=tuple(unresolved),
            conflicting_candidates=tuple(conflicts),
            ledger_record_refs=(),
            legacy_import=legacy_import,
            evidence_refs=tuple(evidence_refs),
            metadata=dict(metadata or {}),
        )
        self.registry.register(envelope)
        return envelope

    def link(
        self,
        parent: Mapping[str, Any],
        *,
        relation: str,
        source_field: str,
        evidence_ref: str,
        evidence_kind: str = "artifact-envelope-v2",
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return parent_link(
            parent_artifact_id=str(parent["artifact_id"]),
            parent_content_hash=str(parent["content_hash"]["value"]),
            relation=relation,
            evidence_kind=evidence_kind,
            source_field=source_field,
            evidence_ref=evidence_ref,
            details=dict(details or {}),
        )

    def create_registry_snapshot(
        self,
        *,
        captured_at: str,
        evidence_refs: Iterable[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = self.boundary.require_write_path(
            self.scenario_root / "snapshots" / "registry_snapshot.json"
        )
        envelope, payload = build_registry_snapshot_envelope(
            self.registry,
            target,
            captured_at=captured_at,
            registered_at=self.registered_at,
            run_ref=self.run_ref,
            evidence_refs=tuple(evidence_refs),
        )
        self.registry.register(envelope)
        return envelope, payload

    def create_policy_snapshot(
        self,
        policy_path: Path | str,
        *,
        policy_id: str,
        policy_version: str,
        policy_schema_version: str,
        created_at: str,
        evidence_refs: Iterable[str],
    ) -> dict[str, Any]:
        source = self.boundary.require_write_path(policy_path)
        envelope = build_compatibility_policy_snapshot_envelope(
            source,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_schema_version=policy_schema_version,
            created_at=created_at,
            registered_at=self.registered_at,
            run_ref=self.run_ref,
            evidence_refs=tuple(evidence_refs),
        )
        self.registry.register(envelope)
        return envelope

    def _write_once(self, path: Path, encoded: bytes) -> None:
        target = self.boundary.require_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != encoded:
                raise ContractValidationError(
                    f"immutable payload already exists with different bytes: {target}"
                )
            return
        with target.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
