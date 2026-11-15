"""Field vector records in the current format, for the field search and structural tests.

Those tests used frozen records made with the first offline vector algorithm, which the
product no longer accepts. The same
17 fields of the same public fixture card (FX-ACTIVE-SELF-LITERATURE-R1, paper FixtureSelf001)
are built here by the product from the public acceptance fixtures.

The structural query pack took each query vector from one of those fields; ``QUERY_FIELDS``
names that field, and ``structural_inputs`` puts the current vector of the same field in. The
injection fixture was edited for the rename after the pack recorded its hash, so the pack
records it again.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

QUERY_PACK = Path(__file__).resolve().parent / "fixtures" / "structural" / "structural_queries.json"
INJECTIONS = QUERY_PACK.with_name("structural_injections.json")

QUERY_FIELDS = {
    ("SA-Q01-METHOD", "method"): "method/0",
    ("SA-Q02-CONCLUSION", "conclusion"): "author_conclusion",
    ("SA-Q03-DATA", "data_numeric"): "key_data/0/metric",
    ("SA-Q04-LIMITATION", "limitations"): "author_limitations_outlook/limitations/0/quote",
    ("SA-Q05-COUNTER", "boundary_counterevidence"): "boundary_conditions/0",
    ("SA-Q06-MULTI", "method"): "method/0",
    ("SA-Q06-MULTI", "limitations"): "author_limitations_outlook/limitations/0/quote",
    ("SA-Q07-SCOPE", "research_question"): "research_question",
    ("SA-Q08-PROVENANCE", "key_results"): "key_results",
    ("SA-Q09-DIVERSITY", "comparison_groups"): "comparison_context/groups/0",
    ("SA-Q10-FILTERED-UNDERFILL", "method"): "method/0",
    ("SA-Q11-COUNTER-UNDERFILL", "missing_counterevidence"): "author_limitations_outlook/limitations/0/quote",
}


# Fields that follow from the vector algorithm or from the source snapshot identity. The frozen
# development oracle has the first algorithm's vectors and a snapshot whose text was renamed
# later, so these differ from it; every other field of a record must still equal the oracle.
REBUILT_FIELDS = frozenset({
    "embedding", "embedding_model_id", "source_snapshot_id", "source_snapshot_hash",
    "field_source_id", "parent_refs", "vector_id", "storage_key", "content_hash",
})


def stable_fields(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["field_path"]: {k: v for k, v in row.items() if k not in REBUILT_FIELDS} for row in rows}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


@lru_cache(maxsize=None)
def _records_text(profile_id: str | None) -> str:
    from tests.modules.retrieval_weighting.field_vector_acceptance.suite import contract_adapter as api

    records = api.vector_bundle(["FX-ACTIVE-SELF-LITERATURE-R1"], profile_id=profile_id)["vectors"]["records"]
    return json.dumps(records, ensure_ascii=False)


def field_records(profile_id: str | None = None) -> list[dict[str, Any]]:
    """A fresh copy of the 17 records on every call; another profile id builds them under that profile."""
    return json.loads(_records_text(profile_id))


def export_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in records
    )


def structural_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], str, str]:
    """(query pack, field records, query pack sha256, field export sha256)."""
    records = field_records()
    by_field = {row["field_path"]: row["embedding"] for row in records}
    pack = json.loads(QUERY_PACK.read_text(encoding="utf-8"))
    for query in pack["queries"]:
        for dimension in query["dimensions"]:
            dimension["query_vector"] = list(by_field[QUERY_FIELDS[(query["query_id"], dimension["dimension_id"])]])
    export_sha = _sha256(export_bytes(records))
    pack["embedding_model_id"] = records[0]["embedding_model_id"]
    pack["field_export_sha256"] = export_sha
    pack["structural_injection_fixture_sha256"] = _sha256(INJECTIONS.read_bytes())
    pack_sha = _sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return pack, records, pack_sha, export_sha
