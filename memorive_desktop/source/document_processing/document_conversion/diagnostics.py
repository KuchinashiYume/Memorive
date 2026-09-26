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


def make_source_marker(kind: str, ordinal: int, *, label: str | None = None) -> str:
    if kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {kind}")
    if not isinstance(ordinal, int) or ordinal < 1:
        raise ValueError("source ordinal must be a positive integer")
    suffix = f" label={json.dumps(label, ensure_ascii=False)}" if label is not None else ""
    return f"<!-- memorive:source:v1 kind={kind} ordinal={ordinal}{suffix} -->"


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


def analyze_text(text: str, *, replacement_fail_ratio: float = 0.20, control_fail_ratio: float = 0.20) -> TextDiagnostics:
    value = text or ""
    content = re.sub(r"<!--\s*memorive:(?:page|source).*?-->", "", value, flags=re.S)
    nonspace = [char for char in content if not char.isspace()]
    denominator = max(len(nonspace), 1)
    replacements = content.count("\ufffd")
    controls = sum(unicodedata.category(char) == "Cc" and char not in "\t\r\n" for char in content)
    replacement_ratio = replacements / denominator
    control_ratio = controls / denominator
    failures: list[str] = []
    degradation: list[str] = []
    if not nonspace:
        failures.append("EMPTY_TEXT_OUTPUT")
    if replacement_ratio >= replacement_fail_ratio:
        failures.append("TEXT_REPLACEMENT_RATIO_EXCEEDED")
    elif replacements:
        degradation.append("TEXT_REPLACEMENT_PRESENT_BELOW_GATE")
    if control_ratio >= control_fail_ratio:
        failures.append("CONTROL_CHARACTER_RATIO_EXCEEDED")
    elif controls:
        degradation.append("CONTROL_CHARACTER_PRESENT_BELOW_GATE")
    return TextDiagnostics(tuple(failures), tuple(degradation), len(nonspace), replacement_ratio, control_ratio)
