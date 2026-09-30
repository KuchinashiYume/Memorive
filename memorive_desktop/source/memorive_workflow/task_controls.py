from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping
import uuid

from memorive_app.contracts import utc_now

from .contracts import Core_NODE_IDS, E2_NODE_IDS, node_ids_for


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_OVERRIDE_KEYS = frozenset(
    {
        "enabled",
        "model",
        "profile_ref",
        "profile_kind",
        "provider",
        "execution_profile",
        "retry_count",
        "fallback_profile_ref",
        "fallback_profile",
    }
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _identifier(value: Any, field: str) -> str:
    rendered = str(value or "")
    if not _IDENTIFIER.fullmatch(rendered):
        raise ValueError(f"TASK_CONTROL_{field.upper()}_INVALID")
    return rendered


def _node_id(value: Any) -> str:
    rendered = str(value or "")
    if rendered not in E2_NODE_IDS:
        raise ValueError("TASK_CONTROL_NODE_INVALID")
    return rendered


def _override(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value or set(value) - _OVERRIDE_KEYS:
        raise ValueError("TASK_CONTROL_OVERRIDE_INVALID")
    accepted = deepcopy(dict(value))
    if "enabled" in accepted and not isinstance(accepted["enabled"], bool):
        raise ValueError("TASK_CONTROL_ENABLED_INVALID")
    if "retry_count" in accepted and (
        isinstance(accepted["retry_count"], bool)
        or not isinstance(accepted["retry_count"], int)
        or not 0 <= accepted["retry_count"] <= 10
    ):
        raise ValueError("TASK_CONTROL_RETRY_COUNT_INVALID")
    model_fields = {"model", "profile_ref", "profile_kind", "provider", "execution_profile"}
    present_model_fields = model_fields & set(accepted)
    if present_model_fields:
        if present_model_fields != model_fields:
            raise ValueError("TASK_CONTROL_MODEL_OVERRIDE_INCOMPLETE")
        if any(not isinstance(accepted[key], str) or not accepted[key].strip() for key in ("model", "profile_ref", "profile_kind", "provider")):
            raise ValueError("TASK_CONTROL_MODEL_OVERRIDE_INVALID")
        profile = accepted["execution_profile"]
        if (
            accepted["profile_kind"] not in {"API", "CLI", "LOCAL"}
            or not isinstance(profile, Mapping)
            or set(profile) != {"service", "model"}
            or not isinstance(profile.get("service"), Mapping)
            or not isinstance(profile.get("model"), Mapping)
            or not isinstance(profile["model"].get("model_name"), str)
        ):
            raise ValueError("TASK_CONTROL_EXECUTION_PROFILE_INVALID")
        accepted["model"] = accepted["model"].strip()
        accepted["profile_ref"] = accepted["profile_ref"].strip()
        accepted["provider"] = accepted["provider"].strip()
        accepted["execution_profile"] = deepcopy(dict(profile))
    if 'fallback_profile_ref' in accepted or 'fallback_profile' in accepted:
        if not {'fallback_profile_ref', 'fallback_profile'}.issubset(accepted):
            raise ValueError('TASK_CONTROL_FALLBACK_INCOMPLETE')
        ref, profile = accepted['fallback_profile_ref'], accepted['fallback_profile']
        if ref is None:
            if profile is not None: raise ValueError('TASK_CONTROL_FALLBACK_INVALID')
        elif (not isinstance(profile, Mapping) or profile.get('profile_ref') != ref):
            raise ValueError('TASK_CONTROL_FALLBACK_INVALID')
        else:
            accepted['fallback_profile'] = _override(profile)
            if accepted.get('profile_ref') == ref:
                raise ValueError('TASK_CONTROL_FALLBACK_DUPLICATE')
    return accepted


class TaskControlStore:
    """Cross-process, job-scoped controls for the packaged Core worker.

    The desktop process writes only resolved profile metadata and retry counts;
    the service process atomically locks a node before its first effect.
    Credential values and document/session content are never stored here.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "core_task_controls.sqlite3"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")
            with connection:
                yield connection
        finally:
            # sqlite3's transaction context commits/rolls back but does not close.
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_topologies (job_id TEXT PRIMARY KEY, nodes_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS node_pause_points (
                    job_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_controls (
                    job_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    overrides_json TEXT NOT NULL,
                    started_nodes_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rollback_requests (
                    request_id TEXT PRIMARY KEY,
                    predecessor_job_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    resume_from_node_id TEXT NOT NULL,
                    workflow_config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    successor_job_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rollback_pending_idx
                ON rollback_requests(status, created_at, request_id);
                """
            )

    @staticmethod
    def _row_state(row: sqlite3.Row | None) -> tuple[int, dict[str, Any], list[str]]:
        if row is None:
            return 0, {}, []
        overrides = json.loads(row["overrides_json"])
        started = json.loads(row["started_nodes_json"])
        if not isinstance(overrides, dict) or not isinstance(started, list):
            raise ValueError("TASK_CONTROL_STORE_CORRUPT")
        return int(row["revision"]), overrides, started

    def overrides(self, job_id: str) -> dict[str, dict[str, Any]]:
        accepted_job = _identifier(job_id, "job_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_controls WHERE job_id=?", (accepted_job,)
            ).fetchone()
        _revision, overrides, _started = self._row_state(row)
        return deepcopy(overrides)

    def started_node_ids(self, job_id: str) -> tuple[str, ...]:
        accepted_job = _identifier(job_id, "job_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_controls WHERE job_id=?", (accepted_job,)
            ).fetchone()
        _revision, _overrides, started = self._row_state(row)
        return tuple(started)

    def set_override(
        self, job_id: str, node_id: str, override: Mapping[str, Any],
        *, allow_started_failed_node: bool = False,
    ) -> dict[str, Any]:
        accepted_job = _identifier(job_id, "job_id")
        accepted_node = _node_id(node_id)
        accepted_override = _override(override)
        if 'fallback_profile_ref' in accepted_override and accepted_node != '01_DOCUMENT_INGEST':
            raise ValueError('TASK_CONTROL_FALLBACK_NODE_INVALID')
        stamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_controls WHERE job_id=?", (accepted_job,)
            ).fetchone()
            revision, overrides, started = self._row_state(row)
            if accepted_node in started and not (
                allow_started_failed_node
                and started
                and started[-1] == accepted_node
            ):
                raise ValueError("TASK_CONTROL_NODE_ALREADY_STARTED")
            merged = dict(overrides.get(accepted_node) or {})
            merged.update(accepted_override)
            overrides[accepted_node] = _override(merged)
            connection.execute(
                """
                INSERT INTO task_controls(job_id, revision, overrides_json, started_nodes_json, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    revision=excluded.revision,
                    overrides_json=excluded.overrides_json,
                    started_nodes_json=excluded.started_nodes_json,
                    updated_at=excluded.updated_at
                """,
                (accepted_job, revision + 1, _json(overrides), _json(started), stamp),
            )
        return {
            "schema_version": "DesktopTaskNodeOverrideReceipt-v1",
            "job_id": accepted_job,
            "node_id": accepted_node,
            "revision": revision + 1,
            "override": deepcopy(overrides[accepted_node]),
            "scope": "TASK_ONLY",
            "credential_values_recorded": False,
            "started_failed_node_override": (
                accepted_node in started and allow_started_failed_node
            ),
            "status": "PASS",
        }

    def clear_override(self, job_id: str, node_id: str) -> dict[str, Any]:
        accepted_job = _identifier(job_id, "job_id")
        accepted_node = _node_id(node_id)
        stamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_controls WHERE job_id=?", (accepted_job,)
            ).fetchone()
            revision, overrides, started = self._row_state(row)
            if accepted_node in started:
                raise ValueError("TASK_CONTROL_NODE_ALREADY_STARTED")
            removed = overrides.pop(accepted_node, None) is not None
            connection.execute(
                """
                INSERT INTO task_controls(job_id, revision, overrides_json, started_nodes_json, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    revision=excluded.revision,
                    overrides_json=excluded.overrides_json,
                    started_nodes_json=excluded.started_nodes_json,
                    updated_at=excluded.updated_at
                """,
                (accepted_job, revision + 1, _json(overrides), _json(started), stamp),
            )
        return {
            "schema_version": "DesktopTaskNodeOverrideResetReceipt-v1",
            "job_id": accepted_job,
            "node_id": accepted_node,
            "restored": removed,
            "revision": revision + 1,
            "status": "PASS",
        }

    def pause_point(self, job_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT node_id FROM node_pause_points WHERE job_id=?",
                                     (_identifier(job_id, "job_id"),)).fetchone()
        return row["node_id"] if row else None

    def set_pause_point(self, job_id: str, node_id: str) -> dict[str, Any]:
        job_id, node_id = _identifier(job_id, "job_id"), _node_id(node_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM task_controls WHERE job_id=?", (job_id,)).fetchone()
            _, _, started = self._row_state(row)
            if node_id in started:
                raise ValueError("TASK_CONTROL_NODE_ALREADY_STARTED")
            prior = connection.execute("SELECT node_id FROM node_pause_points WHERE job_id=?", (job_id,)).fetchone()
            if prior and prior["node_id"] != node_id:
                raise ValueError("TASK_PAUSE_ALREADY_SET")
            connection.execute("INSERT OR IGNORE INTO node_pause_points VALUES(?,?,?)", (job_id,node_id,utc_now()))
        return {"job_id":job_id,"node_id":node_id,"status":"PAUSE_SCHEDULED"}

    def clear_pause_point(self, job_id: str, node_id: str | None = None, *, resume=None) -> dict[str, Any]:
        job_id = _identifier(job_id, "job_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT node_id FROM node_pause_points WHERE job_id=?", (job_id,)).fetchone()
            if row and node_id and row["node_id"] != _node_id(node_id):
                raise ValueError("TASK_PAUSE_NODE_CHANGED")
            # Serialize resume with the worker's start check. No lost wakeup:
            # either the reservation is removed before entry or the paused job resumes.
            if resume is not None:
                resume()
            connection.execute("DELETE FROM node_pause_points WHERE job_id=?", (job_id,))
        return {"job_id":job_id,"status":"PAUSE_CLEARED"}

    def bind_topology(self, job_id, definition):
        nodes=node_ids_for(definition)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT nodes_json FROM task_topologies WHERE job_id=?',(job_id,)).fetchone()
            if old and tuple(json.loads(old['nodes_json']))!=nodes:
                raise ValueError('TASK_CONTROL_TOPOLOGY_IMMUTABLE')
            existing=db.execute('SELECT started_nodes_json FROM task_controls WHERE job_id=?',(job_id,)).fetchone()
            started=json.loads(existing[0]) if existing else []
            if started!=list(nodes[:len(started)]):raise ValueError('TASK_CONTROL_TOPOLOGY_PREFIX_CONFLICT')
            db.execute('INSERT OR IGNORE INTO task_topologies VALUES(?,?)',(job_id,_json(nodes)))

    def mark_started(self, job_id: str, node_id: str, *, pause_callback=None) -> dict[str, Any]:
        accepted_job = _identifier(job_id, "job_id")
        accepted_node = _node_id(node_id)
        stamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_controls WHERE job_id=?", (accepted_job,)
            ).fetchone()
            revision, overrides, started = self._row_state(row)
            if accepted_node not in started:
                point = connection.execute("SELECT node_id FROM node_pause_points WHERE job_id=?", (accepted_job,)).fetchone()
                if point and point["node_id"] == accepted_node:
                    if pause_callback is not None:
                        pause_callback()
                    return {"job_id":accepted_job,"node_id":accepted_node,"status":"PAUSED"}
                topology=connection.execute("SELECT nodes_json FROM task_topologies WHERE job_id=?",(accepted_job,)).fetchone()
                nodes=tuple(json.loads(topology[0])) if topology else Core_NODE_IDS
                expected = nodes[len(started)] if len(started) < len(nodes) else None
                if accepted_node != expected:
                    raise ValueError(
                        f"TASK_CONTROL_NODE_START_ORDER_INVALID:{accepted_node}:{expected}"
                    )
                started.append(accepted_node)
                revision += 1
                connection.execute(
                    """
                    INSERT INTO task_controls(job_id, revision, overrides_json, started_nodes_json, updated_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        revision=excluded.revision,
                        overrides_json=excluded.overrides_json,
                        started_nodes_json=excluded.started_nodes_json,
                        updated_at=excluded.updated_at
                    """,
                    (accepted_job, revision, _json(overrides), _json(started), stamp),
                )
        return {
            "schema_version": "DesktopTaskNodeStartedReceipt-v1",
            "job_id": accepted_job,
            "node_id": accepted_node,
            "started_node_ids": list(started),
            "revision": revision,
            "status": "PASS",
        }

    def effective_workflow_config(
        self, job_id: str, workflow_config: Mapping[str, Any]
    ) -> dict[str, Any]:
        accepted = deepcopy(dict(workflow_config))
        nodes = accepted.get("nodes")
        if not isinstance(nodes, Mapping):
            raise ValueError("TASK_CONTROL_WORKFLOW_CONFIG_INVALID")
        accepted["nodes"] = {
            str(key): deepcopy(dict(value))
            for key, value in nodes.items()
            if isinstance(value, Mapping)
        }
        for node_id, override in self.overrides(job_id).items():
            if node_id not in accepted["nodes"]:
                raise ValueError("TASK_CONTROL_WORKFLOW_NODE_MISSING")
            accepted["nodes"][node_id].update(deepcopy(override))
        embedding = accepted["nodes"].get("02_CHUNK_EMBEDDING")
        context = accepted["nodes"].get("06_CONTEXT_PACK")
        if isinstance(embedding, Mapping) and isinstance(context, dict):
            for field in (
                "model",
                "profile_ref",
                "profile_kind",
                "provider",
                "execution_profile",
            ):
                if field in embedding:
                    context[field] = deepcopy(embedding[field])
            context["profile_source_node_id"] = "02_CHUNK_EMBEDDING"
            context["configuration_source"] = "INHERITED_NODE_PROFILE"
        return accepted

    def request_rollback(
        self,
        *,
        predecessor_job_id: str,
        item_id: str,
        resume_from_node_id: str,
        workflow_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        predecessor = _identifier(predecessor_job_id, "predecessor_job_id")
        accepted_item = _identifier(item_id, "item_id")
        resume = _node_id(resume_from_node_id)
        if not isinstance(workflow_config, Mapping) or not isinstance(workflow_config.get("nodes"), Mapping):
            raise ValueError("TASK_CONTROL_ROLLBACK_WORKFLOW_INVALID")
        stamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM rollback_requests
                WHERE (item_id=? AND status='PENDING') OR
                  (predecessor_job_id=? AND resume_from_node_id=? AND status='DISPATCHED')
                ORDER BY created_at DESC LIMIT 1
                """,
                (accepted_item, predecessor, resume),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            nonce = uuid.uuid4().hex
            request_id = "rollback_" + hashlib.sha256(
                f"{predecessor}\0{accepted_item}\0{resume}\0{nonce}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """
                INSERT INTO rollback_requests(
                    request_id, predecessor_job_id, item_id, resume_from_node_id,
                    workflow_config_json, status, successor_job_id, error_code,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,'PENDING',NULL,NULL,?,?)
                """,
                (
                    request_id,
                    predecessor,
                    accepted_item,
                    resume,
                    _json(dict(workflow_config)),
                    stamp,
                    stamp,
                ),
            )
        return {
            "request_id": request_id,
            "predecessor_job_id": predecessor,
            "item_id": accepted_item,
            "resume_from_node_id": resume,
            "status": "PENDING",
            "successor_job_id": None,
            "error_code": None,
            "created_at": stamp,
            "updated_at": stamp,
        }

    def list_rollback_requests(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT request_id, status, successor_job_id FROM rollback_requests").fetchall()
        return [dict(row) for row in rows]

    def recovery_requests(self, item_id: str) -> list[dict[str, Any]]:
        accepted = _identifier(item_id, "item_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_id, predecessor_job_id, item_id, resume_from_node_id, "
                "status, successor_job_id, error_code, created_at, updated_at "
                "FROM rollback_requests WHERE item_id=? ORDER BY created_at, request_id", (accepted,)
            ).fetchall()
        return [dict(row) for row in rows]

    def recovery_request(self, request_id: str) -> dict[str, Any]:
        accepted = _identifier(request_id, "request_id")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM rollback_requests WHERE request_id=?", (accepted,)).fetchone()
        if row is None:
            raise ValueError("CURRENT_TASK_RESTART_REQUEST_NOT_FOUND")
        result = dict(row)
        result.pop("workflow_config_json", None)
        return result

    def next_rollback_request(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM rollback_requests
                WHERE status='PENDING' ORDER BY created_at, request_id LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["workflow_config"] = json.loads(result.pop("workflow_config_json"))
        return result

    def update_rollback(
        self,
        request_id: str,
        *,
        status: str,
        successor_job_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        accepted_request = _identifier(request_id, "rollback_request_id")
        if status not in {"DISPATCHED", "SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("TASK_CONTROL_ROLLBACK_STATUS_INVALID")
        if successor_job_id is not None:
            successor_job_id = _identifier(successor_job_id, "successor_job_id")
        stamp = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM rollback_requests WHERE request_id=?",
                (accepted_request,),
            ).fetchone()
            if row is None:
                raise KeyError(accepted_request)
            connection.execute(
                """
                UPDATE rollback_requests
                SET status=?, successor_job_id=COALESCE(?, successor_job_id),
                    error_code=?, updated_at=? WHERE request_id=?
                """,
                (status, successor_job_id, error_code, stamp, accepted_request),
            )
            updated = connection.execute(
                "SELECT * FROM rollback_requests WHERE request_id=?",
                (accepted_request,),
            ).fetchone()
        result = dict(updated)
        result.pop("workflow_config_json", None)
        return result


__all__ = ["TaskControlStore"]
