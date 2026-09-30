"""Thin ASSET-MIGRATION maintenance facade; no duplicated SQL or Registry authority."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .migration import MigrationExecutor, MigrationPlanner, MigrationReconciler
from .migration_contracts import canonical_digest
from .query_projection import (
    QueryProjectionBuilder,
    RegistryQueryService,
    projection_source_fingerprint,
)
from .registry_v2 import ArtifactRegistry


class RegistryDoctor:
    def __init__(
        self,
        registry: ArtifactRegistry,
        projection_path: Path | str,
        *,
        builder: QueryProjectionBuilder,
        registry_root_class: str = "ISOLATED_SANDBOX",
    ) -> None:
        self.registry = registry
        self.projection_path = Path(projection_path).resolve()
        self.builder = builder
        self.registry_root_class = registry_root_class

    def status(self) -> dict[str, Any]:
        events = self.registry.read_events()
        artifacts = self.registry.artifacts(effective=True)
        fingerprint = projection_source_fingerprint(
            self.registry, registry_root_class=self.registry_root_class
        )
        projection: dict[str, Any]
        if not self.projection_path.is_file():
            projection = {"status": "MISSING", "fresh": False}
        else:
            try:
                projection = self.builder.verify(
                    self.registry,
                    self.projection_path,
                    registry_root_class=self.registry_root_class,
                )
            except Exception as exc:
                projection = {
                    "status": getattr(exc, "code", type(exc).__name__),
                    "fresh": False,
                    "error_type": type(exc).__name__,
                }
        result = {
            "status_schema_version": "asset_migration-registry-status-v1",
            "registry_root_class": self.registry_root_class,
            "event_count": len(events),
            "artifact_count": len(artifacts),
            "integrity_issues": self.registry.audit_integrity(),
            "source_fingerprint": fingerprint,
            "projection": projection,
            "registry_authoritative": True,
            "projection_authoritative": False,
            "reverse_write_api": False,
        }
        result["status_digest"] = canonical_digest(result)
        return result

    def rebuild_projection(self) -> dict[str, Any]:
        return self.builder.build(
            self.registry,
            self.projection_path,
            registry_root_class=self.registry_root_class,
        )

    def verify_projection(self) -> dict[str, Any]:
        return self.builder.verify(
            self.registry,
            self.projection_path,
            registry_root_class=self.registry_root_class,
        )

    def remove_projection_for_rebuild(self) -> dict[str, Any]:
        self.builder._validate_target(self.projection_path)
        existed = self.projection_path.is_file()
        before = None
        if existed:
            raw = self.projection_path.read_bytes()
            before = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest().upper()}
            self.projection_path.unlink()
        return {
            "status": "REMOVED_FOR_REBUILD" if existed else "ALREADY_MISSING",
            "removed_projection": before,
            "registry_writes": 0,
            "authoritative_fact_loss": 0,
        }


class RegistryMaintenanceService:
    def __init__(
        self,
        *,
        registry_root: Path | str,
        projection_path: Path | str,
        allowed_sandbox_root: Path | str,
        production_roots: tuple[Path | str, ...] = (),
        rebuild_authorized: bool = False,
    ) -> None:
        self.registry = ArtifactRegistry(registry_root)
        self.projection_path = Path(projection_path).resolve()
        self.allowed_sandbox_root = Path(allowed_sandbox_root).resolve()
        self.planner = MigrationPlanner(
            allowed_sandbox_root=self.allowed_sandbox_root,
            production_roots=production_roots,
        )
        self.executor = MigrationExecutor(
            allowed_sandbox_root=self.allowed_sandbox_root,
            production_roots=production_roots,
        )
        self.reconciler = MigrationReconciler()
        self.builder = QueryProjectionBuilder(
            allowed_projection_root=self.allowed_sandbox_root,
            production_roots=production_roots,
        )
        self.query = RegistryQueryService(
            self.registry,
            self.projection_path,
            builder=self.builder,
            rebuild_authorized=rebuild_authorized,
        )
        self.doctor = RegistryDoctor(
            self.registry,
            self.projection_path,
            builder=self.builder,
        )

    def plan_migration(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        return self.planner.plan(manifest, self.registry.root)

    def apply_or_resume(
        self,
        manifest: Mapping[str, Any],
        plan: Mapping[str, Any],
        *,
        receipt_path: Path | str,
        fault_hook: Any = None,
    ) -> dict[str, Any]:
        return self.executor.apply_or_resume(
            manifest,
            plan,
            target_registry_root=self.registry.root,
            receipt_path=receipt_path,
            fault_hook=fault_hook,
        )

    def registry_status(self) -> dict[str, Any]:
        return self.doctor.status()

    def find_artifacts(
        self, filters: Mapping[str, Any] | None = None, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self.query.find_artifacts(filters, limit=limit, offset=offset)

    def trace_lineage(
        self,
        artifact_id: str,
        *,
        direction: str = "parents",
        max_depth: int = 1,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        return self.query.trace_lineage(
            artifact_id,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    def list_unresolved(
        self, filters: Mapping[str, Any] | None = None, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self.query.list_unresolved(filters, limit=limit, offset=offset)

    def rebuild_projection(self) -> dict[str, Any]:
        return self.doctor.rebuild_projection()

    def verify_projection(self) -> dict[str, Any]:
        return self.doctor.verify_projection()


def human_summary(result: Mapping[str, Any]) -> str:
    """Render only from the same typed result; never recompute a verdict."""

    digest = result.get("result_digest") or result.get("status_digest") or result.get(
        "build_receipt_sha256"
    )
    kind = result.get("query_kind") or result.get("status") or result.get("build_status")
    count = result.get("returned_count", result.get("artifact_count", result.get("row_counts")))
    return f"kind={kind}; count={count}; digest={digest}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASSET-MIGRATION sandbox-only Registry maintenance")
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--registry-root", required=True)
    parser.add_argument("--projection", required=True)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("rebuild")
    sub.add_parser("verify")
    find = sub.add_parser("find")
    find.add_argument("--artifact-id")
    find.add_argument("--artifact-type")
    find.add_argument("--limit", type=int, default=50)
    lineage = sub.add_parser("lineage")
    lineage.add_argument("artifact_id")
    lineage.add_argument("--direction", choices=("parents", "children", "both"), default="parents")
    lineage.add_argument("--max-depth", type=int, default=1)
    unresolved = sub.add_parser("unresolved")
    unresolved.add_argument("--reason-code")
    unresolved.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    service = RegistryMaintenanceService(
        registry_root=args.registry_root,
        projection_path=args.projection,
        allowed_sandbox_root=args.sandbox_root,
        rebuild_authorized=False,
    )
    if args.command == "status":
        result = service.registry_status()
    elif args.command == "rebuild":
        result = service.rebuild_projection()
    elif args.command == "verify":
        result = service.verify_projection()
    elif args.command == "find":
        filters = {
            key: value
            for key, value in {
                "artifact_id": args.artifact_id,
                "artifact_type": args.artifact_type,
            }.items()
            if value is not None
        }
        result = service.find_artifacts(filters, limit=args.limit)
    elif args.command == "lineage":
        result = service.trace_lineage(
            args.artifact_id,
            direction=args.direction,
            max_depth=args.max_depth,
        )
    else:
        filters = {"reason_code": args.reason_code} if args.reason_code else {}
        result = service.list_unresolved(filters, limit=args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(human_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
