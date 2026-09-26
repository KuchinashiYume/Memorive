"""Frozen Card Schema v6 field registry and loss-limited read-only expansion."""

from __future__ import annotations

import unicodedata
from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_json_bytes
from .contracts import (
    make_hashed_payload,
    make_value_hash,
    require_non_empty_refs,
    require_non_empty_string,
)
from .errors import ContractViolation


_CORE_SCALARS = (
    "research_question",
    "key_results",
    "author_conclusion",
)
_CORE_LISTS = (
    "research_object",
    "method",
    "boundary_conditions",
)
_COMPARISON_SCALARS = (
    "object",
    "condition",
    "time",
    "scale",
    "scenario",
    "applicability",
)
_REGISTRY_PAYLOAD: dict[str, Any] = {
    "object_type": "FieldRegistry",
    "schema_version": "1.0",
    "registry_id": "p03_t03_card_schema_v6_field_registry",
    "revision": 1,
    "card_schema_version": 6,
    "canonicalization": "unicode_nfkc_then_whitespace_collapse_v1",
    "allowed_doc_types": ["literature", "note"],
    "active_is_not_verified": True,
    "claim_type_persisted": False,
    "fields": [
        {"field_path_template": "research_question", "value_kind": "string", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "research_object/{item}", "value_kind": "string_list_item", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "method/{item}", "value_kind": "string_list_item", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "key_results", "value_kind": "string", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "author_conclusion", "value_kind": "string", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "boundary_conditions/{item}", "value_kind": "string_list_item", "source_rule": "core_by_field_anchor"},
        {"field_path_template": "comparison_context/object", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/condition", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/groups/{item}", "value_kind": "string_list_item", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/time", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/scale", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/scenario", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "comparison_context/applicability", "value_kind": "string", "source_rule": "card_artifact_level"},
        {"field_path_template": "key_data/{item}/metric", "value_kind": "string", "source_rule": "key_data_item_anchor"},
        {"field_path_template": "author_limitations_outlook/limitations/{item}/quote", "value_kind": "string", "source_rule": "supplemental_item_anchor"},
        {"field_path_template": "author_limitations_outlook/outlook/{item}/quote", "value_kind": "string", "source_rule": "supplemental_item_anchor"},
    ],
}


def get_field_registry() -> dict[str, Any]:
    """Return a fresh typed-hash copy of the complete T03 field registry."""

    return make_hashed_payload(_REGISTRY_PAYLOAD)


def canonicalize_field_text(value: Any, field_path: str) -> str:
    """Apply only NFKC and whitespace collapse to a non-empty field string."""

    if not isinstance(value, str) or not value:
        raise ContractViolation(f"{field_path} must be a non-empty string")
    canonical = " ".join(unicodedata.normalize("NFKC", value).split())
    if not canonical:
        raise ContractViolation(f"{field_path} is empty after canonicalization")
    return canonical


def _completion_and_omissions(card: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    completion = card.get("completion_status")
    if completion not in {"complete", "passed_with_omissions"}:
        raise ContractViolation("completion_status must be complete or passed_with_omissions")
    omissions = card.get("omissions")
    if not isinstance(omissions, list):
        raise ContractViolation("omissions must be a list")
    if completion == "complete" and omissions:
        raise ContractViolation("complete Card must have omissions=[]")
    if completion == "passed_with_omissions" and not omissions:
        raise ContractViolation("passed_with_omissions requires at least one tombstone")
    checked: list[Mapping[str, Any]] = []
    for ordinal, omission in enumerate(omissions):
        if not isinstance(omission, Mapping):
            raise ContractViolation(f"omissions[{ordinal}] must be an object")
        require_non_empty_string(omission.get("location"), f"omissions[{ordinal}].location")
        if "removed_value" not in omission:
            raise ContractViolation(f"omissions[{ordinal}].removed_value is required")
        checked.append(omission)
    return checked


def _is_omitted(field_root: str, omissions: list[Mapping[str, Any]]) -> bool:
    return any(
        omission["location"] == field_root
        or str(omission["location"]).startswith(field_root + "[")
        for omission in omissions
    )


def _reject_live_tombstone(
    field_path: str,
    field_root: str,
    source_value: Any,
    omissions: list[Mapping[str, Any]],
) -> None:
    locations = {field_root, field_path, field_path.replace("/", ".")}
    source_bytes = canonical_json_bytes(source_value)
    for omission in omissions:
        if omission["location"] not in locations:
            continue
        try:
            removed = canonical_json_bytes(omission["removed_value"])
        except (TypeError, ValueError) as exc:
            raise ContractViolation("omission.removed_value is outside the JCS domain") from exc
        if removed == source_bytes:
            raise ContractViolation(f"tombstoned value remains live at {field_path}")


def _core_source_refs(card: Mapping[str, Any], field_root: str) -> list[str]:
    source_anchor = card.get("source_anchor")
    if not isinstance(source_anchor, Mapping):
        raise ContractViolation("source_anchor must be an object")
    by_field = source_anchor.get("by_field")
    if not isinstance(by_field, Mapping) or field_root not in by_field:
        raise ContractViolation(f"source_anchor.by_field.{field_root} is required")
    raw = by_field[field_root]
    refs: list[str] = []
    if isinstance(raw, list):
        for ordinal, anchor in enumerate(raw):
            if not isinstance(anchor, Mapping):
                raise ContractViolation(f"{field_root} anchor[{ordinal}] must be an object")
            refs.append(
                require_non_empty_string(
                    anchor.get("chunk_id"), f"{field_root}.anchor[{ordinal}].chunk_id"
                )
            )
    elif isinstance(raw, Mapping):
        chunk_ids = raw.get("chunk_ids")
        if not isinstance(chunk_ids, list) or not chunk_ids:
            raise ContractViolation(f"legacy {field_root}.chunk_ids must be non-empty")
        refs.extend(
            require_non_empty_string(chunk_id, f"{field_root}.chunk_ids[]")
            for chunk_id in chunk_ids
        )
    else:
        raise ContractViolation(f"source_anchor.by_field.{field_root} has unknown shape")
    return require_non_empty_refs(refs, f"source_anchor.by_field.{field_root}.refs")


def _item_source_refs(item: Mapping[str, Any], field_path: str) -> list[str]:
    refs: list[str] = []
    for key in ("source_ref", "chunk_id"):
        if key in item and item[key] is not None:
            refs.append(require_non_empty_string(item[key], f"{field_path}.{key}"))
    if not refs:
        raise ContractViolation(f"{field_path} has no explicit item source reference")
    return require_non_empty_refs(refs, f"{field_path}.source_refs")


def _field_item(
    *,
    field_path: str,
    field_root: str,
    source_value: Any,
    source_refs: list[str],
    source_coverage: str,
    omissions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    _reject_live_tombstone(field_path, field_root, source_value, omissions)
    canonical_value = canonicalize_field_text(source_value, field_path)
    return {
        "field_path": field_path,
        "value_locator": "/" + field_path,
        "canonical_value": canonical_value,
        "value_hash": make_value_hash(canonical_value),
        "source_refs": require_non_empty_refs(source_refs, f"{field_path}.source_refs"),
        "source_coverage": source_coverage,
    }


def expand_card_fields(
    card_payload: Mapping[str, Any],
    *,
    card_artifact_ref: str,
) -> list[dict[str, Any]]:
    """Expand every authorized Card Schema v6 field into one item per list value."""

    if not isinstance(card_payload, Mapping):
        raise ContractViolation("card_payload must be an object")
    card = card_payload
    if card.get("schema_version") != 6:
        raise ContractViolation("only Card Schema v6 is accepted")
    if "doc_type" in card:
        raise ContractViolation("doc_type must remain in its authority sidecar")
    if "claim_type" in card:
        raise ContractViolation("Claim Type is coverage-limited and must not be persisted")
    require_non_empty_string(card_artifact_ref, "card_artifact_ref")
    omissions = _completion_and_omissions(card)
    results: list[dict[str, Any]] = []

    for field_root in _CORE_SCALARS:
        value = card.get(field_root)
        if value is None:
            if not _is_omitted(field_root, omissions):
                raise ContractViolation(f"required core field {field_root} is absent")
            continue
        results.append(
            _field_item(
                field_path=field_root,
                field_root=field_root,
                source_value=value,
                source_refs=_core_source_refs(card, field_root),
                source_coverage="field_anchor",
                omissions=omissions,
            )
        )

    for field_root in _CORE_LISTS:
        values = card.get(field_root)
        if values is None:
            values = []
        if not isinstance(values, list):
            raise ContractViolation(f"{field_root} must be a list")
        if not values:
            if not _is_omitted(field_root, omissions):
                raise ContractViolation(f"required core list {field_root} is empty")
            continue
        source_refs = _core_source_refs(card, field_root)
        for ordinal, value in enumerate(values):
            results.append(
                _field_item(
                    field_path=f"{field_root}/{ordinal}",
                    field_root=field_root,
                    source_value=value,
                    source_refs=source_refs,
                    source_coverage="field_anchor",
                    omissions=omissions,
                )
            )

    comparison = card.get("comparison_context")
    if comparison is not None:
        if not isinstance(comparison, Mapping):
            raise ContractViolation("comparison_context must be an object")
        unexpected = set(comparison) - set(_COMPARISON_SCALARS) - {"groups"}
        if unexpected:
            raise ContractViolation("comparison_context contains unexpected keys")
        for leaf in _COMPARISON_SCALARS:
            value = comparison.get(leaf)
            if value in {None, ""}:
                continue
            field_path = f"comparison_context/{leaf}"
            results.append(
                _field_item(
                    field_path=field_path,
                    field_root=field_path,
                    source_value=value,
                    source_refs=[card_artifact_ref],
                    source_coverage="artifact_level_no_dedicated_anchor",
                    omissions=omissions,
                )
            )
        groups = comparison.get("groups", [])
        if not isinstance(groups, list):
            raise ContractViolation("comparison_context.groups must be a list")
        for ordinal, value in enumerate(groups):
            field_path = f"comparison_context/groups/{ordinal}"
            results.append(
                _field_item(
                    field_path=field_path,
                    field_root="comparison_context/groups",
                    source_value=value,
                    source_refs=[card_artifact_ref],
                    source_coverage="artifact_level_no_dedicated_anchor",
                    omissions=omissions,
                )
            )

    key_data = card.get("key_data", [])
    if not isinstance(key_data, list):
        raise ContractViolation("key_data must be a list")
    for ordinal, item in enumerate(key_data):
        if not isinstance(item, Mapping):
            raise ContractViolation(f"key_data[{ordinal}] must be an object")
        field_path = f"key_data/{ordinal}/metric"
        results.append(
            _field_item(
                field_path=field_path,
                field_root="key_data",
                source_value=item.get("metric"),
                source_refs=_item_source_refs(item, field_path),
                source_coverage="item_anchor",
                omissions=omissions,
            )
        )

    supplemental = card.get("author_limitations_outlook", {})
    if not isinstance(supplemental, Mapping):
        raise ContractViolation("author_limitations_outlook must be an object")
    unexpected = set(supplemental) - {"limitations", "outlook"}
    if unexpected:
        raise ContractViolation("author_limitations_outlook contains unexpected keys")
    for leaf in ("limitations", "outlook"):
        items = supplemental.get(leaf, [])
        if not isinstance(items, list):
            raise ContractViolation(f"author_limitations_outlook.{leaf} must be a list")
        for ordinal, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise ContractViolation(f"{leaf}[{ordinal}] must be an object")
            field_path = f"author_limitations_outlook/{leaf}/{ordinal}/quote"
            results.append(
                _field_item(
                    field_path=field_path,
                    field_root=f"author_limitations_outlook/{leaf}",
                    source_value=item.get("quote"),
                    source_refs=_item_source_refs(item, field_path),
                    source_coverage="item_anchor",
                    omissions=omissions,
                )
            )

    return sorted(results, key=lambda item: item["field_path"])
