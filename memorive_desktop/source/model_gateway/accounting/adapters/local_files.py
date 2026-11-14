"""Strict adapters for already-provided local JSON and CSV settlement evidence."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..contracts import AccountingContractError, SettlementObservation


def _inside(path: Path, allowed_root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError as exc:
        raise AccountingContractError("settlement input is outside the frozen offline root") from exc
    if not resolved.is_file():
        raise AccountingContractError("settlement input must be an existing file")
    return resolved


def load_json_observations(path: Path | str, *, allowed_root: Path | str) -> list[SettlementObservation]:
    source = _inside(Path(path), Path(allowed_root))
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountingContractError("invalid UTF-8 settlement JSON") from exc
    rows = raw.get("observations") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise AccountingContractError("settlement JSON must be a list or observations object")
    return [SettlementObservation.from_mapping(row) for row in rows]


def load_csv_observations(path: Path | str, *, allowed_root: Path | str) -> list[SettlementObservation]:
    source = _inside(Path(path), Path(allowed_root))
    with source.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result: list[SettlementObservation] = []
    for row in rows:
        normalized = {key: (value if value != "" else None) for key, value in row.items()}
        if normalized.get("rounding_decimal_places") is not None:
            normalized["rounding_decimal_places"] = int(normalized["rounding_decimal_places"])
        result.append(SettlementObservation.from_mapping(normalized))
    return result
