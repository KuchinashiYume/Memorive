"""Shared deterministic diagnostics and typed source marker helpers."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass


SOURCE_KINDS = {"page", "slide", "sheet", "block", "line", "row", "frame"}
_SOURCE_RE = re.compile(
    r'^\s*<!--\s*memorive:source:v1\s+kind=(?P<kind>page|slide|sheet|block|line|row|frame)'
    r'\s+ordinal=(?P<ordinal>[1-9]\d*)'
    r'(?:\s+label=(?P<label>"(?:[^"\\]|\\.)*"))?\s*-->\s*$'
)


def parse_source_marker(value: str) -> dict[str, object] | None:
    match = _SOURCE_RE.fullmatch(value)
    if match is None:
        return None
    label = json.loads(match.group("label")) if match.group("label") else None
    return {"kind": match.group("kind"), "ordinal": int(match.group("ordinal")), "label": label}


@dataclass(frozen=True)
class TextDiagnostics:
    hard_failures: tuple[str, ...]
    degradation_signals: tuple[str, ...]
    nonspace_characters: int
    replacement_ratio: float
    control_ratio: float
