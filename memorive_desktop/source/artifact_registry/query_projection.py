"""Disposable SQLite projection and bounded query facade for ASSET-MIGRATION.

The projection is never authoritative: every query rebinds it to an exact
Registry fingerprint, stale data is never returned, and no API in this module
writes back to Registry JSONL.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .migration import read_registry_events_without_side_effects
from .migration_contracts import (
    PROJECTION_SCHEMA_VERSION,
    QUERY_RESULT_SCHEMA_VERSION,
    canonical_bytes,
    canonical_digest,
    now_iso,
    sha256_bytes,
)
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    ENVELOPE_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    canonical_json,
    content_hash_value,
    lineage_status,
    resolved_parent_links,
)


DEFAULT_LIMIT = 50
MAXIMUM_LIMIT = 500
DEFAULT_LINEAGE_DEPTH = 1
MAXIMUM_LINEAGE_DEPTH = 8
MAXIMUM_LINEAGE_NODES = 500


class ProjectionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable
        self.blocking = True


class ProjectionStaleError(ProjectionError):
    pass


class ProjectionCorruptError(ProjectionError):
    pass


def projection_source_fingerprint(
    registry: ArtifactRegistry,
    *,
    registry_root_class: str = "ISOLATED_SANDBOX",
    builder_code_path: Path | str | None = None,
) -> dict[str, Any]:
    events = read_registry_events_without_side_effects(registry.root)
    normalized = b"".join(canonical_bytes(event) for event in events)
    registry_hash = sha256_bytes(normalized)
    annual: list[dict[str, Any]] = []
    if registry.root.is_dir():
        for path in sorted(registry.root.glob("[0-9][0-9][0-9][0-9].jsonl")):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            annual.append(
                {"name": path.name, "bytes": len(raw), "raw_sha256": sha256_bytes(raw)}
            )
    last_record_id = events[-1]["event_id"] if events else None
    snapshot_seed = canonical_json(
        {
            "last_record_id": last_record_id,
            "record_count": len(events),
            "registry_content_hash": registry_hash,
            "annual_file_manifest": annual,
        }
    )
    code_path = Path(builder_code_path) if builder_code_path is not None else Path(__file__)
    return {
        "registry_root_class": registry_root_class,
        "registry_snapshot_id": "snapshot-" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:32],
        "last_record_id": last_record_id,
        "record_count": len(events),
        "registry_content_hash": registry_hash,
        "annual_file_manifest": annual,
        "envelope_schema_versions": sorted(
            {
                event["payload"]["schema_version"]
                for event in events
                if event["event_type"] == "artifact_registered"
            }
        ),
        "event_schema_versions": sorted({event["event_schema_version"] for event in events}),
        "current_envelope_schema_version": ENVELOPE_SCHEMA_VERSION,
        "current_event_schema_version": EVENT_SCHEMA_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "builder_code_sha256": sha256_bytes(code_path.read_bytes()),
    }


class QueryProjectionBuilder:
    def __init__(
        self,
        *,
        allowed_projection_root: Path | str,
        production_roots: tuple[Path | str, ...] = (),
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self.allowed_projection_root = Path(allowed_projection_root).resolve()
        self.production_roots = tuple(Path(path).resolve() for path in production_roots)
        self.clock = clock

    def build(
        self,
        registry: ArtifactRegistry,
        projection_path: Path | str,
        *,
        registry_root_class: str = "ISOLATED_SANDBOX",
    ) -> dict[str, Any]:
        target = Path(projection_path).resolve()
        self._validate_target(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source_before = projection_source_fingerprint(
            registry, registry_root_class=registry_root_class
        )
        events = read_registry_events_without_side_effects(registry.root)
        artifacts = registry.artifacts(effective=True)
        temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        old = _file_snapshot(target)
        built_at = self.clock()
        try:
            connection = sqlite3.connect(temp)
            try:
                self._create_schema(connection)
                self._load(connection, events, artifacts)
                source_after_load = projection_source_fingerprint(
                    registry, registry_root_class=registry_root_class
                )
                if canonical_json(source_before) != canonical_json(source_after_load):
                    raise ProjectionError(
                        "SOURCE_CHANGED_DURING_BUILD",
                        "Registry fingerprint changed during projection build",
                    )
                data_digest = _projection_data_digest(connection)
                row_counts = _row_counts(connection)
                meta = {
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "source_fingerprint": source_before,
                    "built_at": built_at,
                    "data_digest": data_digest,
                    "row_counts": row_counts,
                }
                connection.execute(
                    "INSERT INTO projection_meta(singleton, projection_schema_version, source_fingerprint_json, built_at, data_digest, row_counts_json) VALUES (1, ?, ?, ?, ?, ?)",
                    (
                        PROJECTION_SCHEMA_VERSION,
                        canonical_json(source_before),
                        built_at,
                        data_digest,
                        canonical_json(row_counts),
                    ),
                )
                connection.commit()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ProjectionCorruptError(
                        "PROJECTION_CORRUPT", f"SQLite integrity_check returned {integrity!r}"
                    )
                _verify_referential_integrity(connection)
            finally:
                connection.close()
            source_before_replace = projection_source_fingerprint(
                registry, registry_root_class=registry_root_class
            )
            if canonical_json(source_before) != canonical_json(source_before_replace):
                raise ProjectionError(
                    "SOURCE_CHANGED_DURING_BUILD",
                    "Registry fingerprint changed before atomic replace",
                )
            os.replace(temp, target)
            _fsync_file(target)
        finally:
            temp.unlink(missing_ok=True)
        receipt = {
            "projection_schema_version": PROJECTION_SCHEMA_VERSION,
            "build_status": "COMPLETED",
            "source_fingerprint": source_before,
            "built_at": built_at,
            "projection_path_class": "RUN_LOCAL_SQLITE",
            "projection_sha256": sha256_bytes(target.read_bytes()),
            "projection_bytes": target.stat().st_size,
            "previous_projection": old,
            "data_digest": data_digest,
            "row_counts": row_counts,
            "atomic_replace": True,
            "authoritative": False,
            "reverse_write_api": False,
        }
        receipt["build_receipt_sha256"] = canonical_digest(receipt)
        return receipt

    def verify(
        self,
        registry: ArtifactRegistry,
        projection_path: Path | str,
        *,
        registry_root_class: str = "ISOLATED_SANDBOX",
    ) -> dict[str, Any]:
        target = Path(projection_path).resolve()
        self._validate_target(target)
        if not target.is_file():
            raise ProjectionError("PROJECTION_MISSING", "projection file does not exist")
        try:
            with closing(_read_only_connection(target)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ProjectionCorruptError(
                        "PROJECTION_CORRUPT", f"SQLite integrity_check returned {integrity!r}"
                    )
                meta = _read_meta(connection)
                if meta["projection_schema_version"] != PROJECTION_SCHEMA_VERSION:
                    raise ProjectionError(
                        "VERSION_REBUILD_REQUIRED", "projection schema version mismatch"
                    )
                current = projection_source_fingerprint(
                    registry, registry_root_class=registry_root_class
                )
                fresh = canonical_json(meta["source_fingerprint"]) == canonical_json(current)
                actual_digest = _projection_data_digest(connection)
                digest_matches = actual_digest == meta["data_digest"]
                _verify_referential_integrity(connection)
                if not digest_matches:
                    raise ProjectionCorruptError(
                        "PROJECTION_CORRUPT", "projection data digest mismatch"
                    )
                return {
                    "status": "FRESH" if fresh else "STALE_REBUILD_REQUIRED",
                    "fresh": fresh,
                    "source_fingerprint": current,
                    "projection_source_fingerprint": meta["source_fingerprint"],
                    "data_digest": actual_digest,
                    "data_digest_matches": digest_matches,
                    "row_counts": _row_counts(connection),
                    "projection_sha256": sha256_bytes(target.read_bytes()),
                }
        except sqlite3.DatabaseError as exc:
            raise ProjectionCorruptError("PROJECTION_CORRUPT", "SQLite file is unreadable") from exc

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            CREATE TABLE projection_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                projection_schema_version TEXT NOT NULL,
                source_fingerprint_json TEXT NOT NULL,
                built_at TEXT NOT NULL,
                data_digest TEXT NOT NULL,
                row_counts_json TEXT NOT NULL
            );
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                event_schema_version TEXT NOT NULL,
                annual_file TEXT NOT NULL,
                logical_line INTEGER NOT NULL
            );
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                envelope_schema_version TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                locator_class TEXT NOT NULL,
                run_ref TEXT,
                created_at TEXT,
                registered_at TEXT NOT NULL,
                lineage_status TEXT NOT NULL,
                status TEXT,
                legacy_import INTEGER NOT NULL CHECK (legacy_import IN (0, 1))
            );
            CREATE TABLE parent_links (
                artifact_id TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                parent_hash TEXT NOT NULL,
                relation TEXT NOT NULL,
                evidence_basis_json TEXT NOT NULL,
                PRIMARY KEY (artifact_id, parent_id, relation, evidence_basis_json),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id),
                FOREIGN KEY (parent_id) REFERENCES artifacts(artifact_id)
            );
            CREATE TABLE unresolved_requirements (
                artifact_id TEXT NOT NULL,
                requirement TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                PRIMARY KEY (artifact_id, requirement),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE TABLE conflicting_candidates (
                artifact_id TEXT NOT NULL,
                requirement TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                PRIMARY KEY (artifact_id, requirement, candidate_id),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE TABLE artifact_papers (
                artifact_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                PRIMARY KEY (artifact_id, paper_id),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE TABLE artifact_sources (
                artifact_id TEXT NOT NULL,
                source_artifact_id TEXT NOT NULL,
                PRIMARY KEY (artifact_id, source_artifact_id),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE INDEX idx_artifacts_type ON artifacts(artifact_type, artifact_id);
            CREATE INDEX idx_artifacts_hash ON artifacts(content_hash, artifact_id);
            CREATE INDEX idx_artifacts_run ON artifacts(run_ref, artifact_id);
            CREATE INDEX idx_artifacts_registered ON artifacts(registered_at, artifact_id);
            CREATE INDEX idx_artifacts_status ON artifacts(status, artifact_id);
            CREATE INDEX idx_parent_parent ON parent_links(parent_id, artifact_id);
            CREATE INDEX idx_unresolved_reason ON unresolved_requirements(reason_code, artifact_id);
            CREATE INDEX idx_papers_paper ON artifact_papers(paper_id, artifact_id);
            """
        )

    def _load(
        self,
        connection: sqlite3.Connection,
        events: list[Mapping[str, Any]],
        artifacts: list[Mapping[str, Any]],
    ) -> None:
        year_lines: dict[str, int] = {}
        for event in events:
            year = f"{datetime.fromisoformat(event['recorded_at'].replace('Z', '+00:00')).year:04d}.jsonl"
            year_lines[year] = year_lines.get(year, 0) + 1
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"],
                    event["event_type"],
                    event["recorded_at"],
                    event["event_schema_version"],
                    year,
                    year_lines[year],
                ),
            )
        for envelope in sorted(artifacts, key=lambda value: value["artifact_id"]):
            metadata = envelope.get("metadata") or {}
            status = metadata.get("status") or metadata.get("review_status")
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope["artifact_id"],
                    envelope["artifact_type"],
                    envelope["schema_version"],
                    content_hash_value(envelope),
                    _locator_class(envelope["locator"]["path"]),
                    envelope.get("run_ref"),
                    envelope.get("created_at"),
                    envelope["registered_at"],
                    lineage_status(envelope),
                    str(status) if status is not None else None,
                    1 if envelope["legacy_import"] else 0,
                ),
            )
            for link in resolved_parent_links(envelope):
                connection.execute(
                    "INSERT INTO parent_links VALUES (?, ?, ?, ?, ?)",
                    (
                        envelope["artifact_id"],
                        link["parent_artifact_id"],
                        link["parent_content_hash"],
                        link["relation"],
                        canonical_json(link["evidence_basis"]),
                    ),
                )
            unresolved, conflicts = _lineage_open_fields(envelope)
            for value in unresolved:
                connection.execute(
                    "INSERT INTO unresolved_requirements VALUES (?, ?, ?, ?)",
                    (
                        envelope["artifact_id"],
                        value["requirement"],
                        value["reason_code"],
                        len(value.get("candidate_artifact_ids", [])),
                    ),
                )
            for value in conflicts:
                for candidate_id in value["candidate_artifact_ids"]:
                    connection.execute(
                        "INSERT INTO conflicting_candidates VALUES (?, ?, ?)",
                        (envelope["artifact_id"], value["requirement"], candidate_id),
                    )
            for paper_id in envelope["source_scope"]["paper_ids"]:
                connection.execute(
                    "INSERT INTO artifact_papers VALUES (?, ?)",
                    (envelope["artifact_id"], paper_id),
                )
            for source_id in envelope["source_scope"]["source_artifact_ids"]:
                connection.execute(
                    "INSERT INTO artifact_sources VALUES (?, ?)",
                    (envelope["artifact_id"], source_id),
                )

    def _validate_target(self, target: Path) -> None:
        if not _same_or_below(target, self.allowed_projection_root):
            raise ProjectionError("PROJECTION_ROOT_ESCAPE", "projection target escapes sandbox")
        if any(_same_or_below(target, root) for root in self.production_roots):
            raise ProjectionError("PRODUCTION_PROJECTION_REJECTED", "production projection root rejected")


class RegistryQueryService:
    def __init__(
        self,
        registry: ArtifactRegistry,
        projection_path: Path | str,
        *,
        builder: QueryProjectionBuilder | None = None,
        rebuild_authorized: bool = False,
        registry_root_class: str = "ISOLATED_SANDBOX",
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self.registry = registry
        self.projection_path = Path(projection_path).resolve()
        self.builder = builder
        self.rebuild_authorized = rebuild_authorized
        self.registry_root_class = registry_root_class
        self.clock = clock

    def find_artifacts(
        self,
        filters: Mapping[str, Any] | None = None,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        canonical_filters = _canonical_filters(filters or {}, {
            "artifact_id",
            "content_hash",
            "artifact_type",
            "run_ref",
            "paper_id",
            "registered_from",
            "registered_to",
            "status",
        })
        limit, offset = _validate_page(limit, offset)
        with self._fresh_connection() as (connection, meta, fingerprint):
            clauses: list[str] = []
            values: list[Any] = []
            join = ""
            field_map = {
                "artifact_id": "a.artifact_id",
                "content_hash": "a.content_hash",
                "artifact_type": "a.artifact_type",
                "run_ref": "a.run_ref",
                "status": "a.status",
            }
            for key, column in field_map.items():
                if key in canonical_filters:
                    clauses.append(f"{column} = ?")
                    values.append(canonical_filters[key])
            if "paper_id" in canonical_filters:
                join = " JOIN artifact_papers p ON p.artifact_id = a.artifact_id"
                clauses.append("p.paper_id = ?")
                values.append(canonical_filters["paper_id"])
            if "registered_from" in canonical_filters:
                clauses.append("a.registered_at >= ?")
                values.append(canonical_filters["registered_from"])
            if "registered_to" in canonical_filters:
                clauses.append("a.registered_at <= ?")
                values.append(canonical_filters["registered_to"])
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            query = (
                "SELECT DISTINCT a.artifact_id, a.artifact_type, a.content_hash, a.run_ref, "
                "a.created_at, a.registered_at, a.lineage_status, a.status "
                f"FROM artifacts a{join}{where} ORDER BY a.artifact_id LIMIT ? OFFSET ?"
            )
            rows = _dict_rows(
                connection.execute(query, values + [limit + 1, offset]),
                (
                    "artifact_id",
                    "artifact_type",
                    "content_hash",
                    "run_ref",
                    "created_at",
                    "registered_at",
                    "lineage_status",
                    "status",
                ),
            )
            has_more = len(rows) > limit
            return self._result(
                "find_artifacts",
                canonical_filters,
                rows=rows[:limit],
                nodes=[],
                edges=[],
                limit=limit,
                offset=offset,
                has_more=has_more,
                meta=meta,
                fingerprint=fingerprint,
            )

    def list_unresolved(
        self,
        filters: Mapping[str, Any] | None = None,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        canonical_filters = _canonical_filters(filters or {}, {"artifact_type", "reason_code"})
        limit, offset = _validate_page(limit, offset)
        with self._fresh_connection() as (connection, meta, fingerprint):
            clauses: list[str] = []
            values: list[Any] = []
            if "artifact_type" in canonical_filters:
                clauses.append("a.artifact_type = ?")
                values.append(canonical_filters["artifact_type"])
            if "reason_code" in canonical_filters:
                clauses.append("u.reason_code = ?")
                values.append(canonical_filters["reason_code"])
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = _dict_rows(
                connection.execute(
                    "SELECT u.artifact_id, a.artifact_type, u.requirement, u.reason_code, u.candidate_count "
                    "FROM unresolved_requirements u JOIN artifacts a ON a.artifact_id = u.artifact_id"
                    f"{where} ORDER BY u.artifact_id, u.requirement LIMIT ? OFFSET ?",
                    values + [limit + 1, offset],
                ),
                ("artifact_id", "artifact_type", "requirement", "reason_code", "candidate_count"),
            )
            has_more = len(rows) > limit
            return self._result(
                "list_unresolved",
                canonical_filters,
                rows=rows[:limit],
                nodes=[],
                edges=[],
                limit=limit,
                offset=offset,
                has_more=has_more,
                meta=meta,
                fingerprint=fingerprint,
            )

    def trace_lineage(
        self,
        artifact_id: str,
        *,
        direction: str = "parents",
        max_depth: int = DEFAULT_LINEAGE_DEPTH,
        max_nodes: int = MAXIMUM_LINEAGE_NODES,
    ) -> dict[str, Any]:
        if direction not in {"parents", "children", "both"}:
            raise ProjectionError("QUERY_FILTER_INVALID", "direction must be parents, children or both")
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= MAXIMUM_LINEAGE_DEPTH:
            raise ProjectionError("QUERY_LIMIT_INVALID", "max_depth is out of bounds")
        if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or not 1 <= max_nodes <= MAXIMUM_LINEAGE_NODES:
            raise ProjectionError("QUERY_LIMIT_INVALID", "max_nodes is out of bounds")
        filters = {
            "artifact_id": artifact_id,
            "direction": direction,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
        }
        with self._fresh_connection() as (connection, meta, fingerprint):
            if connection.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone() is None:
                raise ProjectionError("ARTIFACT_NOT_FOUND", f"unknown artifact_id {artifact_id}")
            queue: list[tuple[str, int]] = [(artifact_id, 0)]
            seen = {artifact_id}
            edges: set[tuple[str, str, str]] = set()
            truncated = False
            while queue:
                current, depth = queue.pop(0)
                if depth >= max_depth:
                    continue
                candidates: list[tuple[str, str, str]] = []
                if direction in {"parents", "both"}:
                    candidates.extend(
                        (current, row[0], row[1])
                        for row in connection.execute(
                            "SELECT parent_id, relation FROM parent_links WHERE artifact_id = ? ORDER BY parent_id, relation",
                            (current,),
                        )
                    )
                if direction in {"children", "both"}:
                    candidates.extend(
                        (row[0], current, row[1])
                        for row in connection.execute(
                            "SELECT artifact_id, relation FROM parent_links WHERE parent_id = ? ORDER BY artifact_id, relation",
                            (current,),
                        )
                    )
                for child, parent, relation in candidates:
                    edges.add((child, parent, relation))
                    neighbor = parent if current == child else child
                    if neighbor not in seen:
                        if len(seen) >= max_nodes:
                            truncated = True
                            continue
                        seen.add(neighbor)
                        queue.append((neighbor, depth + 1))
            nodes = _dict_rows(
                connection.execute(
                    "SELECT artifact_id, artifact_type, content_hash, lineage_status FROM artifacts "
                    f"WHERE artifact_id IN ({','.join('?' for _ in seen)}) ORDER BY artifact_id",
                    sorted(seen),
                ),
                ("artifact_id", "artifact_type", "content_hash", "lineage_status"),
            )
            edge_rows = [
                {"child_artifact_id": child, "parent_artifact_id": parent, "relation": relation}
                for child, parent, relation in sorted(edges)
            ]
            warnings = ["MAX_NODES_REACHED"] if truncated else []
            return self._result(
                "trace_lineage",
                filters,
                rows=[],
                nodes=nodes,
                edges=edge_rows,
                limit=max_nodes,
                offset=0,
                has_more=truncated,
                meta=meta,
                fingerprint=fingerprint,
                warnings=warnings,
            )

    def _fresh_connection(self) -> "_ConnectionContext":
        self._ensure_projection()
        return _ConnectionContext(self.projection_path, self.registry, self.registry_root_class)

    def _ensure_projection(self) -> None:
        if not self.projection_path.is_file():
            if self.rebuild_authorized and self.builder is not None:
                self.builder.build(
                    self.registry,
                    self.projection_path,
                    registry_root_class=self.registry_root_class,
                )
                return
            raise ProjectionStaleError("STALE_REBUILD_REQUIRED", "projection is missing")
        try:
            with closing(_read_only_connection(self.projection_path)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise sqlite3.DatabaseError(integrity)
                meta = _read_meta(connection)
        except (sqlite3.DatabaseError, ProjectionError):
            if self.rebuild_authorized and self.builder is not None:
                self.builder.build(
                    self.registry,
                    self.projection_path,
                    registry_root_class=self.registry_root_class,
                )
                return
            raise ProjectionCorruptError("PROJECTION_CORRUPT", "projection requires rebuild")
        if meta["projection_schema_version"] != PROJECTION_SCHEMA_VERSION:
            if self.rebuild_authorized and self.builder is not None:
                self.builder.build(
                    self.registry,
                    self.projection_path,
                    registry_root_class=self.registry_root_class,
                )
                return
            raise ProjectionStaleError("VERSION_REBUILD_REQUIRED", "projection version mismatch")
        current = projection_source_fingerprint(
            self.registry, registry_root_class=self.registry_root_class
        )
        if canonical_json(meta["source_fingerprint"]) != canonical_json(current):
            if self.rebuild_authorized and self.builder is not None:
                self.builder.build(
                    self.registry,
                    self.projection_path,
                    registry_root_class=self.registry_root_class,
                )
                return
            raise ProjectionStaleError(
                "STALE_REBUILD_REQUIRED", "projection source fingerprint is stale"
            )

    def _result(
        self,
        kind: str,
        filters: Mapping[str, Any],
        *,
        rows: list[dict[str, Any]],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        limit: int,
        offset: int,
        has_more: bool,
        meta: Mapping[str, Any],
        fingerprint: Mapping[str, Any],
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        query_seed = {
            "kind": kind,
            "filters": copy.deepcopy(dict(filters)),
            "source_snapshot_id": fingerprint["registry_snapshot_id"],
            "limit": limit,
            "offset": offset,
        }
        result = {
            "query_schema_version": QUERY_RESULT_SCHEMA_VERSION,
            "query_id": "query-" + hashlib.sha256(canonical_bytes(query_seed)).hexdigest()[:24],
            "query_kind": kind,
            "filters": copy.deepcopy(dict(filters)),
            "source": {
                "registry_snapshot_id": fingerprint["registry_snapshot_id"],
                "last_record_id": fingerprint["last_record_id"],
                "record_count": fingerprint["record_count"],
                "registry_content_hash": fingerprint["registry_content_hash"],
            },
            "projection_version": meta["projection_schema_version"],
            "freshness": "FRESH",
            "limit": limit,
            "offset": offset,
            "returned_count": len(rows) if kind != "trace_lineage" else len(nodes),
            "has_more": has_more,
            "rows": rows,
            "nodes": nodes,
            "edges": edges,
            "generated_at": self.clock(),
            "warnings": list(warnings or []),
        }
        result["result_digest"] = query_result_digest(result)
        return result


def query_result_digest(result: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(result))
    candidate.pop("generated_at", None)
    candidate.pop("result_digest", None)
    return canonical_digest(candidate)


class _ConnectionContext:
    def __init__(self, path: Path, registry: ArtifactRegistry, root_class: str) -> None:
        self.path = path
        self.registry = registry
        self.root_class = root_class
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> tuple[sqlite3.Connection, dict[str, Any], dict[str, Any]]:
        self.connection = _read_only_connection(self.path)
        meta = _read_meta(self.connection)
        fingerprint = projection_source_fingerprint(
            self.registry, registry_root_class=self.root_class
        )
        if canonical_json(meta["source_fingerprint"]) != canonical_json(fingerprint):
            self.connection.close()
            raise ProjectionStaleError(
                "STALE_REBUILD_REQUIRED", "Registry changed between freshness check and query"
            )
        return self.connection, meta, fingerprint

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.connection is not None:
            self.connection.close()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _read_meta(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        row = connection.execute(
            "SELECT projection_schema_version, source_fingerprint_json, built_at, data_digest, row_counts_json FROM projection_meta WHERE singleton = 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise ProjectionCorruptError("PROJECTION_CORRUPT", "projection_meta is unavailable") from exc
    if row is None:
        raise ProjectionCorruptError("PROJECTION_CORRUPT", "projection_meta row is missing")
    try:
        return {
            "projection_schema_version": row[0],
            "source_fingerprint": json.loads(row[1]),
            "built_at": row[2],
            "data_digest": row[3],
            "row_counts": json.loads(row[4]),
        }
    except json.JSONDecodeError as exc:
        raise ProjectionCorruptError("PROJECTION_CORRUPT", "projection_meta JSON is invalid") from exc


def _projection_data_digest(connection: sqlite3.Connection) -> str:
    tables = (
        "events",
        "artifacts",
        "parent_links",
        "unresolved_requirements",
        "conflicting_candidates",
        "artifact_papers",
        "artifact_sources",
    )
    payload: dict[str, list[list[Any]]] = {}
    for table in tables:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        order = ", ".join(columns)
        payload[table] = [list(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    return canonical_digest(payload)


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "events",
            "artifacts",
            "parent_links",
            "unresolved_requirements",
            "conflicting_candidates",
            "artifact_papers",
            "artifact_sources",
        )
    }


def _verify_referential_integrity(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ProjectionCorruptError(
            "PROJECTION_CORRUPT", f"foreign key violations: {len(violations)}"
        )


def _lineage_open_fields(
    envelope: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION:
        return (
            list(envelope["unresolved_parent_requirements"]),
            list(envelope["conflicting_candidates"]),
        )
    return (
        list(envelope["lineage"]["unresolved_parent_requirements"]),
        list(envelope["lineage"]["conflicting_candidates"]),
    )


def _locator_class(locator: str) -> str:
    path = Path(locator)
    return "ABSOLUTE_LOCAL" if path.is_absolute() else "RELATIVE_LOCAL"


def _canonical_filters(filters: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    if not isinstance(filters, Mapping):
        raise ProjectionError("QUERY_FILTER_INVALID", "filters must be an object")
    unknown = set(filters) - allowed
    if unknown:
        raise ProjectionError("QUERY_FILTER_INVALID", f"unknown filters: {sorted(unknown)}")
    result: dict[str, Any] = {}
    for key in sorted(filters):
        value = filters[key]
        if not isinstance(value, str) or not value:
            raise ProjectionError("QUERY_FILTER_INVALID", f"{key} must be non-empty text")
        result[key] = value
    return result


def _validate_page(limit: int, offset: int) -> tuple[int, int]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAXIMUM_LIMIT:
        raise ProjectionError("QUERY_LIMIT_INVALID", "limit is out of bounds")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ProjectionError("QUERY_OFFSET_INVALID", "offset must be a non-negative integer")
    return limit, offset


def _dict_rows(cursor: sqlite3.Cursor, fields: Iterable[str]) -> list[dict[str, Any]]:
    names = tuple(fields)
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _file_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": sha256_bytes(raw)}


def _fsync_file(path: Path) -> None:
    # Windows rejects fsync on a read-only file descriptor.
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_or_below(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
