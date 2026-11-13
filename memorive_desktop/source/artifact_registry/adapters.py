"""Read-only Core adapters for the ARTIFACT_REGISTRY candidate registry."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .errors import AdapterError
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    build_envelope,
    parent_link,
    sha256_file,
)

_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")
_TAGS = ("PDF", "RawMD", "CleanMD", "Chunks", "Card", "Analysis")
_TYPES = {
    "PDF": "source_pdf",
    "RawMD": "raw_markdown",
    "CleanMD": "clean_markdown",
    "Chunks": "chunks_jsonl",
    "Card": "card_markdown",
    "Analysis": "analysis_markdown",
}


@dataclass(frozen=True)
class AdaptationResult:
    envelopes: tuple[dict[str, Any], ...]
    mapping_summary: dict[str, Any]

    def by_type(self, artifact_type: str) -> dict[str, Any]:
        hits = [x for x in self.envelopes if x["artifact_type"] == artifact_type]
        if len(hits) != 1:
            raise KeyError(f"expected one {artifact_type!r} envelope, found {len(hits)}")
        return copy.deepcopy(hits[0])


def _read_chunks(path: Path, *, expected_paper_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"invalid Chunks JSONL at line {line_no}: {path}") from exc
        if not isinstance(value, dict):
            raise AdapterError(f"Chunks line {line_no} is not an object: {path}")
        for field in ("paper_id", "chunk_id", "source_file", "chunk_hash", "text"):
            _required_text(value, field, path)
        if value["paper_id"] != expected_paper_id:
            raise AdapterError(f"Chunks paper_id mismatch at line {line_no}: {path}")
        if value["chunk_id"] in seen_ids:
            raise AdapterError(f"duplicate chunk_id {value['chunk_id']!r}: {path}")
        seen_ids.add(value["chunk_id"])
        records.append(value)
    if not records:
        raise AdapterError(f"Chunks file is empty: {path}")
    return records


def _required_text(mapping: Mapping[str, Any], field: str, path: Path) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field} must be non-empty: {path}")
    return value
