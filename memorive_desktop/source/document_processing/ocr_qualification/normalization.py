"""Deterministic normalization for page-scoped OCR/vision output."""

from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any, Mapping

from .contracts import (
    OCRQualificationContractError,
    canonical_sha256,
    validate_normalized_page_transcription,
    validate_page_subject_input,
)


_OUTPUT_FIELDS = {
    "schema_version",
    "page_id",
    "source_image_sha256",
    "candidate_key",
    "terminal_state",
    "literal_text",
    "ordered_blocks",
    "critical_items",
    "table_cells",
    "visual_labels",
    "illegible_spans",
    "warnings",
    "visibility",
}

_CONTENT_TOKEN_ROLES = {
    "body",
    "note",
    "methods",
    "results",
    "references",
    "formula",
    "table_row",
    "chart_item",
    "caption",
}
_UPPER_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_-]{1,31})(?![A-Za-z0-9_-])"
)
_SIGNED_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])([+-]?(?:\d+\.\d+|\d+|\.\d+))(?![A-Za-z0-9_-])"
)
_CHART_CAPTION_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9 _-]{0,40}\s*=\s*[+-]?(?:\d+\.\d+|\d+|\.\d+)\.?"
)


def _text(value: Any, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise OCRQualificationContractError("TEXT_FIELD_INVALID", [field])
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
    if not allow_empty and not normalized:
        raise OCRQualificationContractError("TEXT_FIELD_EMPTY", [field])
    return normalized


def normalize_transcription(
    payload: Mapping[str, Any],
    page_subject_input: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize a provider/local payload without inventing missing fields.

    The local envelope is authoritative for page/image/candidate identity.  Any
    mismatch fails before the object can reach a scorer.
    """

    envelope = validate_page_subject_input(page_subject_input)
    if not isinstance(payload, Mapping):
        raise OCRQualificationContractError("NORMALIZED_PAYLOAD_TYPE_INVALID")
    value = dict(payload)
    expected = {
        "page_id": envelope["page_id"],
        "source_image_sha256": envelope["source_image_sha256"],
        "candidate_key": envelope["candidate"]["candidate_key"],
    }
    mismatches = [field for field, expected_value in expected.items() if value.get(field) != expected_value]
    if mismatches:
        raise OCRQualificationContractError("SOURCE_ENVELOPE_MISMATCH", mismatches)

    return normalize_provider_payload(value)


def normalize_provider_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize already anchor-checked provider output deterministically.

    This layer never reads Gold.  It only canonicalizes emitted fields and
    derives mechanically visible critical items and chart labels from the same
    emitted blocks.  Derived values are exact contiguous substrings of those
    blocks; no Gold, hidden text, or external context is consulted.
    """

    if not isinstance(payload, Mapping):
        raise OCRQualificationContractError("NORMALIZED_PAYLOAD_TYPE_INVALID")
    unknown = sorted(set(payload) - _OUTPUT_FIELDS)
    missing = sorted(_OUTPUT_FIELDS - set(payload))
    if unknown:
        raise OCRQualificationContractError("NORMALIZED_PAYLOAD_FIELDS_UNKNOWN", unknown)
    if missing:
        raise OCRQualificationContractError("NORMALIZED_PAYLOAD_FIELDS_MISSING", missing)
    value = deepcopy(dict(payload))

    blocks = []
    for index, block in enumerate(value["ordered_blocks"]):
        item = dict(block)
        item["block_id"] = _text(item.get("block_id"), f"ordered_blocks/{index}/block_id", allow_empty=False)
        item["role"] = _text(item.get("role"), f"ordered_blocks/{index}/role", allow_empty=False)
        item["text"] = _text(item.get("text"), f"ordered_blocks/{index}/text")
        blocks.append(item)
    value["ordered_blocks"] = sorted(blocks, key=lambda item: item["reading_order"])
    if any(item["role"] == "chart_item" for item in value["ordered_blocks"]):
        for item in value["ordered_blocks"]:
            if item["role"] == "body" and _CHART_CAPTION_PATTERN.fullmatch(item["text"]):
                item["role"] = "caption"
    value["literal_text"] = "\n".join(item["text"] for item in value["ordered_blocks"])

    critical = []
    for index, raw in enumerate(value["critical_items"]):
        item = dict(raw)
        item["value"] = _text(item.get("value"), f"critical_items/{index}/value", allow_empty=False)
        item["block_id"] = _text(item.get("block_id"), f"critical_items/{index}/block_id", allow_empty=False)
        critical.append(item)
    value["critical_items"] = sorted(
        critical,
        key=lambda item: (item["block_id"], item["kind"], item["value"], item["confidence_state"]),
    )

    tables = []
    for index, raw in enumerate(value["table_cells"]):
        item = dict(raw)
        item["table_id"] = _text(item.get("table_id"), f"table_cells/{index}/table_id", allow_empty=False)
        item["text"] = _text(item.get("text"), f"table_cells/{index}/text")
        tables.append(item)
    value["table_cells"] = sorted(
        tables, key=lambda item: (item["table_id"], item["row"], item["column"])
    )

    table_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in value["table_cells"]:
        table_rows.setdefault((item["table_id"], item["row"]), []).append(item)
    existing_critical = {
        (item["kind"], item["value"], item["block_id"], item["confidence_state"])
        for item in value["critical_items"]
    }
    for cells in table_rows.values():
        row_text = " | ".join(
            item["text"] for item in sorted(cells, key=lambda item: item["column"])
        )
        block = next(
            (
                item
                for item in value["ordered_blocks"]
                if item["role"] == "table_row" and item["text"] == row_text
            ),
            None,
        )
        if block is None:
            continue
        for cell in cells:
            token = cell["text"].strip()
            if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?%?", token):
                continue
            kind = "UNIT" if token.endswith("%") else "NUMBER"
            key = (kind, token, block["block_id"], "ASSERTED")
            if key not in existing_critical:
                value["critical_items"].append(
                    {
                        "kind": kind,
                        "value": token,
                        "block_id": block["block_id"],
                        "confidence_state": "ASSERTED",
                    }
                )
                existing_critical.add(key)

    # Provider responses can transcribe a value perfectly in ordered_blocks yet
    # omit the redundant critical_items projection.  Recover only conservative,
    # mechanically visible token classes from content-bearing blocks.
    for block in value["ordered_blocks"]:
        if block["role"] not in _CONTENT_TOKEN_ROLES:
            continue
        derived = [
            ("TOKEN", match.group(1))
            for match in _UPPER_TOKEN_PATTERN.finditer(block["text"])
        ]
        derived.extend(
            ("NUMBER", match.group(1))
            for match in _SIGNED_NUMBER_PATTERN.finditer(block["text"])
        )
        for kind, token in derived:
            key = (kind, token, block["block_id"], "ASSERTED")
            if key in existing_critical:
                continue
            value["critical_items"].append(
                {
                    "kind": kind,
                    "value": token,
                    "block_id": block["block_id"],
                    "confidence_state": "ASSERTED",
                }
            )
            existing_critical.add(key)
    value["critical_items"] = sorted(
        value["critical_items"],
        key=lambda item: (item["block_id"], item["kind"], item["value"], item["confidence_state"]),
    )

    labels = []
    for index, raw in enumerate(value["visual_labels"]):
        item = dict(raw)
        item["role"] = _text(item.get("role"), f"visual_labels/{index}/role", allow_empty=False)
        item["text"] = _text(item.get("text"), f"visual_labels/{index}/text")
        labels.append(item)

    # When chart blocks are present, their canonical ``label | value`` form and
    # signed numeric caption tokens are deterministic visual-label projections.
    # This closes representational omissions without attempting OCR or guessing.
    existing_labels = {(item["role"], item["text"]) for item in labels}
    if any(item["role"] == "chart_item" for item in value["ordered_blocks"]):
        for block in value["ordered_blocks"]:
            derived_labels: list[str] = []
            if block["role"] == "chart_item" and " | " in block["text"]:
                label, _, rendered_value = block["text"].partition(" | ")
                if label:
                    derived_labels.append(label)
                derived_labels.extend(
                    match.group(1)
                    for match in _SIGNED_NUMBER_PATTERN.finditer(rendered_value)
                )
            elif block["role"] == "caption":
                derived_labels.extend(
                    match.group(1)
                    for match in _SIGNED_NUMBER_PATTERN.finditer(block["text"])
                )
            for token in derived_labels:
                key = ("chart_label", token)
                if key in existing_labels:
                    continue
                labels.append({"role": "chart_label", "text": token, "bbox": None})
                existing_labels.add(key)
    value["visual_labels"] = sorted(labels, key=lambda item: (item["role"], item["text"]))

    illegible = []
    for index, raw in enumerate(value["illegible_spans"]):
        item = dict(raw)
        item["block_id"] = _text(item.get("block_id"), f"illegible_spans/{index}/block_id", allow_empty=False)
        item["reason_code"] = _text(item.get("reason_code"), f"illegible_spans/{index}/reason_code", allow_empty=False)
        illegible.append(item)
    value["illegible_spans"] = sorted(
        illegible, key=lambda item: (item["block_id"], item["reason_code"])
    )
    value["warnings"] = sorted(
        {_text(item, "warnings", allow_empty=False) for item in value["warnings"]}
    )
    return validate_normalized_page_transcription(value)


def normalized_transcription_sha256(value: Mapping[str, Any]) -> str:
    return canonical_sha256(validate_normalized_page_transcription(value))
