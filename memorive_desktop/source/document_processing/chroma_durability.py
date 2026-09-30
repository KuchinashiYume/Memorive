"""Restart-safe guard and compact completion checkpoints for desktop Chroma."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable
import uuid

from .config import COLLECTION, chroma_dir


REQUIRED_HNSW_FILES = frozenset(
    {"data_level0.bin", "header.bin", "index_metadata.pickle", "length.bin", "link_lists.bin"}
)
ERROR_CODE = "CHROMA_HNSW_INDEX_NOT_RESTART_DURABLE"
_SESSION_WRITABLE_ROOTS: set[str] = set()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _ids_sha256(ids: Iterable[str]) -> str:
    payload = json.dumps(sorted(ids), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def inspect_chroma_restart_durability(target: str = "sandbox", *, root: Path | None = None) -> dict[str, Any]:
    """Inspect SQLite and HNSW files without constructing a Chroma client."""
    store = (root or chroma_dir(target)).resolve(strict=False)
    if str(store).casefold() in _SESSION_WRITABLE_ROOTS:
        return {"status": "SESSION_WRITABLE", "store": str(store)}
    database_path = store / "chroma.sqlite3"
    if not database_path.is_file():
        return {"status": "EMPTY", "store": str(store), "sqlite_row_count": 0}
    try:
        with closing(sqlite3.connect(database_path)) as database:
            collection = database.execute(
                "SELECT id FROM collections WHERE name = ?", (COLLECTION[target],)
            ).fetchone()
            if collection is None:
                return {"status": "EMPTY", "store": str(store), "sqlite_row_count": 0}
            metadata_segment = database.execute(
                "SELECT id FROM segments WHERE collection = ? AND type = 'urn:chroma:segment/metadata/sqlite'",
                (collection[0],),
            ).fetchone()
            vector_segment = database.execute(
                "SELECT id FROM segments WHERE collection = ? AND type = 'urn:chroma:segment/vector/hnsw-local-persisted'",
                (collection[0],),
            ).fetchone()
            row_count = (
                int(database.execute("SELECT COUNT(*) FROM embeddings WHERE segment_id = ?", (metadata_segment[0],)).fetchone()[0])
                if metadata_segment is not None else 0
            )
    except sqlite3.Error as error:
        return {"status": "SQLITE_INSPECTION_FAILED", "store": str(store), "error_type": type(error).__name__}
    if row_count == 0:
        return {"status": "EMPTY", "store": str(store), "sqlite_row_count": 0}
    segment_id = str(vector_segment[0]) if vector_segment is not None else None
    segment_root = store / segment_id if segment_id else None
    present = (
        {path.name for path in segment_root.iterdir() if path.is_file()}
        if segment_root is not None and segment_root.is_dir() else set()
    )
    missing = sorted(REQUIRED_HNSW_FILES - present)
    if missing:
        return {
            "status": ERROR_CODE,
            "reason_code": ERROR_CODE,
            "store": str(store),
            "sqlite_row_count": row_count,
            "segment_id": segment_id,
            "missing_index_files": missing,
        }
    return {"status": "READY", "store": str(store), "sqlite_row_count": row_count, "segment_id": segment_id}


def _load_chunks(chunks_path: Path) -> tuple[str, list[str], str]:
    rows = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("CHUNKS_EMPTY")
    paper_id = rows[0].get("paper_id")
    ids = [row.get("chunk_id") for row in rows]
    if (
        not isinstance(paper_id, str) or not paper_id
        or any(not isinstance(item, str) or not item for item in ids)
        or len(set(ids)) != len(ids)
        or any(row.get("paper_id") != paper_id for row in rows)
    ):
        raise ValueError("CHUNKS_IDENTITY_INVALID")
    return paper_id, ids, _sha256_file(chunks_path)


def _checkpoint_path(chunks_path: Path, target: str, paper_id: str) -> Path:
    safe_paper = "".join(character if character.isalnum() or character in "._-" else "_" for character in paper_id)[:96]
    source_sha = _sha256_file(chunks_path)
    return chroma_dir(target).parent / "embedding-checkpoints" / f"{safe_paper}_{source_sha[:20].lower()}.json"


def embedding_checkpoint_status(chunks_path: Path | str, *, target: str = "sandbox") -> dict[str, Any]:
    path = Path(chunks_path)
    paper_id, ids, source_sha = _load_chunks(path)
    checkpoint = _checkpoint_path(path, target, paper_id)
    if not checkpoint.is_file():
        return {"status": "MISSING", "paper_id": paper_id, "path": str(checkpoint)}
    try:
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "INVALID", "paper_id": paper_id, "path": str(checkpoint)}
    expected = {
        "paper_id": paper_id,
        "chunks_sha256": source_sha,
        "chunk_count": len(ids),
        "chunk_ids_sha256": _ids_sha256(ids),
    }
    if any(value.get(key) != item for key, item in expected.items()):
        return {"status": "INVALID", "paper_id": paper_id, "path": str(checkpoint)}
    return {
        "status": "READY", "paper_id": paper_id, "path": str(checkpoint),
        "source": value.get("source"), "chunk_count": len(ids),
    }


def _sqlite_candidates(target: str) -> list[Path]:
    active = chroma_dir(target).resolve(strict=False)
    values = [active / "chroma.sqlite3"]
    quarantine = active.parent / "chroma-quarantine"
    if quarantine.is_dir():
        values.extend(
            item / "chroma.sqlite3"
            for item in sorted(quarantine.glob(f"{active.name}_*"), key=lambda path: path.stat().st_mtime, reverse=True)
            if item.is_dir()
        )
    return values


def _paper_ids_from_sqlite(database_path: Path, *, target: str, paper_id: str) -> set[str]:
    if not database_path.is_file():
        return set()
    with closing(sqlite3.connect(database_path)) as database:
        rows = database.execute(
            """
            SELECT e.embedding_id
            FROM collections AS c
            JOIN segments AS s ON s.collection = c.id
            JOIN embeddings AS e ON e.segment_id = s.id
            JOIN embedding_metadata AS m ON m.id = e.id
            WHERE c.name = ?
              AND s.type = 'urn:chroma:segment/metadata/sqlite'
              AND m.key = 'paper_id'
              AND m.string_value = ?
            """,
            (COLLECTION[target], paper_id),
        ).fetchall()
    return {str(row[0]) for row in rows}


def recover_embedding_checkpoint(chunks_path: Path | str, *, target: str = "sandbox") -> dict[str, Any]:
    """Recover a compact marker only when SQLite has the exact chunk member set."""
    current = embedding_checkpoint_status(chunks_path, target=target)
    if current["status"] == "READY":
        return current
    path = Path(chunks_path)
    paper_id, ids, source_sha = _load_chunks(path)
    expected = set(ids)
    for database_path in _sqlite_candidates(target):
        try:
            observed = _paper_ids_from_sqlite(database_path, target=target, paper_id=paper_id)
        except sqlite3.Error:
            continue
        if observed != expected:
            continue
        checkpoint = _checkpoint_path(path, target, paper_id)
        _atomic_json(
            checkpoint,
            {
                "schema_version": "Desktop_CHROMA_DURABLE_EMBEDDING_CHECKPOINT_V1",
                "paper_id": paper_id,
                "chunks_sha256": source_sha,
                "chunk_count": len(ids),
                "chunk_ids_sha256": _ids_sha256(ids),
                "source": "SQLITE_EXACT_MEMBER_RECOVERY",
                "source_database_sha256": _sha256_file(database_path),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return embedding_checkpoint_status(path, target=target)
    return current


def write_embedding_checkpoint(
    chunks_path: Path | str, *, target: str, embedding_run_id: str, embedding_model: str
) -> dict[str, Any]:
    path = Path(chunks_path)
    paper_id, ids, source_sha = _load_chunks(path)
    checkpoint = _checkpoint_path(path, target, paper_id)
    _atomic_json(
        checkpoint,
        {
            "schema_version": "Desktop_CHROMA_DURABLE_EMBEDDING_CHECKPOINT_V1",
            "paper_id": paper_id,
            "chunks_sha256": source_sha,
            "chunk_count": len(ids),
            "chunk_ids_sha256": _ids_sha256(ids),
            "source": "COMPLETED_EMBEDDING_WRITE",
            "embedding_run_id": embedding_run_id,
            "embedding_model": embedding_model,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return embedding_checkpoint_status(path, target=target)


def ensure_chroma_writable(target: str = "sandbox") -> dict[str, Any]:
    """Quarantine a restart-unsafe store before any Chroma client is built."""
    store = chroma_dir(target).resolve(strict=False)
    state = inspect_chroma_restart_durability(target, root=store)
    if state["status"] != ERROR_CODE:
        _SESSION_WRITABLE_ROOTS.add(str(store).casefold())
        return {"action": "UNCHANGED", "state": state}
    quarantine_parent = store.parent / "chroma-quarantine"
    quarantine_parent.mkdir(parents=True, exist_ok=True)
    destination = quarantine_parent / f"{store.name}_{_utc_stamp()}_{uuid.uuid4().hex[:8]}"
    last_error: OSError | None = None
    for attempt in range(20):
        try:
            os.replace(store, destination)
            last_error = None
            break
        except PermissionError as error:
            last_error = error
            if attempt < 19:
                time.sleep(0.25)
    if last_error is not None:
        raise RuntimeError(
            f"{ERROR_CODE}:QUARANTINE_FAILED:{type(last_error).__name__}"
        ) from last_error
    try:
        store.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise RuntimeError(f"{ERROR_CODE}:RECREATE_FAILED:{type(error).__name__}") from error
    receipt = {
        "schema_version": "Desktop_CHROMA_RESTART_RECOVERY_V1",
        "reason_code": ERROR_CODE,
        "source": str(store),
        "quarantine": str(destination),
        "sqlite_row_count": state.get("sqlite_row_count", 0),
        "missing_index_files": state.get("missing_index_files", []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = store.parent / "chroma-recovery" / f"{destination.name}.json"
    _atomic_json(receipt_path, receipt)
    _SESSION_WRITABLE_ROOTS.add(str(store).casefold())
    return {"action": "QUARANTINED", "state": state, "receipt": str(receipt_path)}


def forget_session_state_for_test() -> None:
    _SESSION_WRITABLE_ROOTS.clear()
