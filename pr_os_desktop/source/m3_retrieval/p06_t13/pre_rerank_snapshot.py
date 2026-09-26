"""Immutable candidate snapshot used as the only T13 fallback source."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Mapping, Sequence

from .candidate_features import canonical_sha256, candidate_identity


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def create_snapshot(
    query_id: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    profile: str,
) -> dict[str, Any]:
    if not query_id or not profile:
        raise ValueError("SNAPSHOT_IDENTITY_INVALID")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, candidate in enumerate(candidates, 1):
        row = deepcopy(dict(candidate))
        identity = candidate_identity(row)
        if identity in seen:
            raise ValueError("SNAPSHOT_CANDIDATE_DUPLICATE")
        seen.add(identity)
        text = row.get("text")
        if not isinstance(text, str):
            raise ValueError("SNAPSHOT_TEXT_INVALID")
        row["candidate_id"] = identity
        row["pre_rerank_rank"] = rank
        rows.append(row)
    exact_view = [
        {
            "candidate_id": row["candidate_id"],
            "paper_id": row.get("paper_id"),
            "chunk_id": row.get("chunk_id"),
            "source_id": row.get("source_id"),
            "text_sha256": _text_sha256(row["text"]),
            "pre_rerank_rank": row["pre_rerank_rank"],
        }
        for row in rows
    ]
    snapshot = {
        "schema_version": "P06T13PreRerankCandidateSnapshot-v1",
        "query_id": query_id,
        "profile": profile,
        "candidate_count": len(rows),
        "candidate_exact_view": exact_view,
        "candidate_exact_set_sha256": canonical_sha256(exact_view),
        "candidates": rows,
    }
    snapshot["snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot


def verify_snapshot(snapshot: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> bool:
    if snapshot.get("schema_version") != "P06T13PreRerankCandidateSnapshot-v1":
        return False
    content = dict(snapshot)
    expected_snapshot_hash = content.pop("snapshot_sha256", None)
    if expected_snapshot_hash != canonical_sha256(content):
        return False
    rebuilt = create_snapshot(
        str(snapshot.get("query_id") or ""),
        candidates,
        profile=str(snapshot.get("profile") or ""),
    )
    return (
        rebuilt["candidate_exact_set_sha256"] == snapshot.get("candidate_exact_set_sha256")
        and rebuilt["candidate_exact_view"] == snapshot.get("candidate_exact_view")
    )


def restore_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list) or not verify_snapshot(snapshot, candidates):
        raise ValueError("SNAPSHOT_MISMATCH")
    return deepcopy(candidates)
