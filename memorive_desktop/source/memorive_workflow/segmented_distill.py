from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from inspect import signature
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import uuid

import yaml

from document_processing.naming import display_title, file_name
from evidence_extraction.card import CONTENT_FIELDS, LIST_FIELDS, build_card, detect_card_lang, write_card
from evidence_extraction.config import CARD_SCHEMA_VERSION, INPUT_MAX_CHARS, now_minute
from evidence_extraction.distill import (
    DistillResult,
    _locate,
    _ownership_gate,
    _read_chunks,
    _read_frontmatter,
    _render_chunks,
    _self_check,
)
from evidence_extraction.segmented_distill import (
    CapacityProfile,
    CapacityRoute,
    SegmentCompletionLedger,
    SegmentShardValidator,
    build_source_manifest,
    build_topology,
    route_document,
)


ESTIMATOR_REVISION = "desktop-workflow-unicode-codepoint-upper-bound-v1"
ADAPTER_PROFILE_REVISION = "Desktop_LONG_DOCUMENT_ADAPTER_V1"
PROMPT_RESERVE_TOKENS = 0  # measured per profile below
REASONING_RESERVE_TOKENS = 0  # already included in output allowance
SAFETY_MARGIN = 1.0


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(_canonical_bytes(dict(value)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = value.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _frontmatter(path: Path) -> dict[str, Any]:
    parts = path.read_text(encoding="utf-8").split("---", 2)
    if len(parts) < 3:
        raise RuntimeError(f"Capabilities_SEGMENT_CARD_FRONTMATTER_INVALID:{path}")
    value = yaml.safe_load(parts[1])
    if not isinstance(value, dict):
        raise RuntimeError(f"Capabilities_SEGMENT_CARD_FRONTMATTER_INVALID:{path}")
    return value


def _validate_replay_card(
    path: Path,
    *,
    source_frontmatter: Mapping[str, Any],
    expected_model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate an already-written Card before treating it as crash recovery."""

    card = _frontmatter(path)
    source_anchor = card.get("source_anchor")
    by_field = source_anchor.get("by_field") if isinstance(source_anchor, Mapping) else None
    if (
        not isinstance(source_anchor, Mapping)
        or source_anchor.get("paper_id") != source_frontmatter.get("paper_id")
        or card.get("title") != source_frontmatter.get("title")
        or card.get("data_ownership") != source_frontmatter.get("data_ownership")
        or card.get("schema_version") != CARD_SCHEMA_VERSION
        or card.get("distill_model") != expected_model
        or not isinstance(by_field, Mapping)
    ):
        raise RuntimeError("Capabilities_SEGMENT_FINAL_REPLAY_IDENTITY_CONFLICT")
    for field in CONTENT_FIELDS:
        value = card.get(field)
        anchors = by_field.get(field)
        valid_value = (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and item.strip() for item in value)
            if field in LIST_FIELDS
            else isinstance(value, str) and bool(value.strip())
        )
        if not valid_value or not isinstance(anchors, list) or not anchors:
            raise RuntimeError(f"Capabilities_SEGMENT_FINAL_REPLAY_FIELD_INVALID:{field}")
    card_lang = detect_card_lang([card.get(field) for field in CONTENT_FIELDS])
    return card, _self_check(card, card_lang)


def _semantic_card_projection(card: Mapping[str, Any]) -> dict[str, Any]:
    """Exclude only run-time stamps/hashes when comparing a rebuilt Card."""

    auxiliary = (
        "comparison_context",
        "author_limitations_outlook",
        "terms",
        "citations",
    )
    source_anchor = card.get("source_anchor")
    return {
        "schema_version": card.get("schema_version"),
        "title": card.get("title"),
        "data_ownership": card.get("data_ownership"),
        "distill_model": card.get("distill_model"),
        **{field: deepcopy(card.get(field)) for field in CONTENT_FIELDS},
        "key_data": deepcopy(card.get("key_data") or []),
        **{field: deepcopy(card.get(field)) for field in auxiliary if field in card},
        "source_anchor": {
            "paper_id": source_anchor.get("paper_id") if isinstance(source_anchor, Mapping) else None,
            "by_field": deepcopy(source_anchor.get("by_field")) if isinstance(source_anchor, Mapping) else None,
        },
    }


def _one(folder: Path, prefix: str, suffix: str) -> Path:
    matches = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.name.startswith(prefix) and path.name.endswith(suffix)
    )
    if len(matches) != 1:
        raise RuntimeError(f"Capabilities_SEGMENT_ARTIFACT_CARDINALITY:{prefix}:{len(matches)}")
    return matches[0]


def _first_positive_integer(values: Sequence[Any]) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _capacity_from_registry(model_name: str) -> tuple[int | None, int | None, str | None]:
    try:
        import model_gateway.capabilities as capability_module

        path = Path(capability_module.DEFAULT_REGISTRY_PATH)
        raw = json.loads(path.read_text(encoding="utf-8"))
        providers = raw.get("providers") if isinstance(raw, Mapping) else None
        if not isinstance(providers, Mapping):
            return None, None, None
        matches = []
        for provider, models in providers.items():
            if isinstance(models, Mapping) and isinstance(models.get(model_name), Mapping):
                value = models[model_name].get("max_input_tokens")
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    output = models[model_name].get("max_output_tokens")
                    if not isinstance(output, int) or isinstance(output, bool) or output <= 0:
                        return None, None, None
                    matches.append((provider, value, output))
        if len(matches) != 1:
            return None, None, None
        provider, value, output = matches[0]
        return value, output, f"model_gateway.model_capabilities:{provider}:{_file_sha256(path)}"
    except Exception:
        return None, None, None


def _resolve_capacity_profile(
    workflow_config: Mapping[str, Any],
) -> tuple[CapacityProfile | None, dict[str, Any]]:
    nodes = workflow_config.get("nodes")
    node = nodes.get("03_CARD_DISTILL") if isinstance(nodes, Mapping) else None
    if not isinstance(node, Mapping):
        raise RuntimeError("Capabilities_SEGMENT_CAPACITY_NODE03_MISSING")
    execution = node.get("execution_profile")
    model = execution.get("model") if isinstance(execution, Mapping) else None
    service = execution.get("service") if isinstance(execution, Mapping) else None
    if not isinstance(model, Mapping) or not isinstance(service, Mapping):
        raise RuntimeError("Capabilities_SEGMENT_CAPACITY_EXECUTION_PROFILE_MISSING")
    model_name = model.get("model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        raise RuntimeError("Capabilities_SEGMENT_CAPACITY_MODEL_NAME_MISSING")

    from model_gateway.resource_limits import describe_capacity
    from evidence_extraction.distill import PromptRegistry, _PROMPTS_ROOT, DISTILL_TASK

    capacity = describe_capacity(profile_kind=node.get('profile_kind'),
        execution_profile=execution)
    context_window = capacity['effective_capacity']['input_tokens']
    if node.get('profile_kind') == 'LOCAL' and capacity['machine_safe_input_tokens'] is None:
        context_window = None  # model maximum is not proof of loaded machine capacity
    output_capacity = capacity['effective_capacity']['output_tokens']
    source = capacity['capacity_source']
    # Same estimator as this adapter; source text is measured separately.
    template = PromptRegistry(_PROMPTS_ROOT).get(DISTILL_TASK)
    prompt_reserve = len(template.body.replace('{input}', ''))
    profile_identity = {
        "adapter_revision": ADAPTER_PROFILE_REVISION,
        "profile_ref": node.get("profile_ref"),
        "profile_kind": node.get("profile_kind"),
        "model_name": model_name,
        "context_window_tokens": context_window,
        "provider_output_capacity_tokens": output_capacity,
        "capacity_source": source,
        "prompt_measurement_source": "CURRENT_DISTILL_PROMPT_CODEPOINT_ESTIMATE",
        "capacity": capacity,
    }
    if (context_window is None or output_capacity is None or source is None
            or context_window - output_capacity - prompt_reserve <= 0):
        return None, {
            **profile_identity,
            "capacity_source": (
                "CLI_PROVIDER_MANAGED_NO_PRODUCT_HARD_LIMIT"
                if node.get("profile_kind") == "CLI"
                else (
                    "LOCAL_RUNTIME_MANAGED_NO_PRODUCT_HARD_LIMIT"
                    if node.get("profile_kind") == "LOCAL"
                    else "API_PROVIDER_MANAGED_NO_PRODUCT_HARD_LIMIT"
                )
            ),
            "numeric_capacity_resolved": False,
            "product_hard_limit_added": False,
        }
    profile = CapacityProfile(
        profile_id=f"desktop-workflow-capabilities-{_sha256(profile_identity)[:16].lower()}",
        context_window_tokens=context_window,
        max_output_tokens=min(output_capacity, context_window),
        prompt_reserve_tokens=prompt_reserve,
        reasoning_reserve_tokens=0,
        safety_margin=1.0,
        segment_core_token_limit=context_window - min(output_capacity, context_window) - prompt_reserve,
        estimator_revision=ESTIMATOR_REVISION,
    )
    profile.validate()
    return profile, {
        **profile_identity,
        **asdict(profile),
        "numeric_capacity_resolved": True,
        "product_hard_limit_added": False,
    }


def _manifest_chunks(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "source_id": str(row.get("source_id") or row["chunk_id"]),
            "page_id": row.get("page_start"),
            "section_id": row.get("section_path"),
            "text": str(row["text"]),
            # This intentionally overestimates most Latin-script inputs and is
            # deterministic across API, CLI and local execution channels.
            "estimated_tokens": max(1, len(str(row["text"]))),
        }
        for row in records
    ]


def _segment_records(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    segment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    original = {str(row["chunk_id"]): dict(row) for row in records}
    source_chunks = {str(row["chunk_id"]): row for row in manifest["chunks"]}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for span in segment["core_source_spans"]:
        grouped.setdefault(str(span["chunk_id"]), []).append(span)
    output = []
    for chunk_id, spans in grouped.items():
        ordered = sorted(spans, key=lambda item: (item["start_offset"], item["end_offset"]))
        start = ordered[0]["start_offset"]
        end = ordered[-1]["end_offset"]
        if any(left["end_offset"] != right["start_offset"] for left, right in zip(ordered, ordered[1:])):
            raise RuntimeError(f"Capabilities_SEGMENT_NON_CONTIGUOUS_SOURCE_SPAN:{chunk_id}")
        text = str(source_chunks[chunk_id]["text"])[start:end]
        if not text.strip():
            raise RuntimeError(f"Capabilities_SEGMENT_EMPTY_SOURCE_SPAN:{chunk_id}")
        output.append({**deepcopy(original[chunk_id]), "text": text})
    if not output:
        raise RuntimeError("Capabilities_SEGMENT_EMPTY_CORE_SET")
    return output


def _dedupe(values: Sequence[Any]) -> list[Any]:
    output = []
    seen = set()
    for value in values:
        identity = _sha256(value)
        if identity not in seen:
            output.append(deepcopy(value))
            seen.add(identity)
    return output


def _executable_topology(records, manifest, core_limit, serialized_limit):
    """Budget the actual legacy input, including IDs, headings and page labels.

    The historical size threshold routes long documents into smaller calls; it
    is not a terminal document-size rejection. Each refinement strictly reduces
    the core budget. Original chunks and source coordinates remain immutable.
    """
    while True:
        topology = build_topology(
            manifest, core_token_limit=core_limit, overlap_unit_count=1,
            max_leaf_segments=64, max_split_depth=2,
        )
        largest = max(len(_render_chunks(_segment_records(records, manifest, part)))
                      for part in topology['segments'])
        if largest <= serialized_limit:
            return topology
        if core_limit <= 1:
            raise RuntimeError('Capabilities_SEGMENT_CAPACITY_UNRESOLVED:SOURCE_LABELS_EXCEED_INPUT_BUDGET')
        core_limit = max(1, min(core_limit - 1, math.floor(core_limit * serialized_limit / largest)))


def _is_output_truncation(result: DistillResult) -> bool:
    error = str(result.error_message or '').lower()
    return result.status == 'failed' and str(result.error_type or '').lower() == 'cardparseerror' and (
        'finish_reason=length' in error or 'finish_reason_length' in error)


def _aggregate_cards(cards: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not cards:
        raise RuntimeError("Capabilities_SEGMENT_CARD_SET_EMPTY")
    paper_ids = {card.get("source_anchor", {}).get("paper_id") for card in cards}
    titles = {card.get("title") for card in cards}
    ownership = {card.get("data_ownership") for card in cards}
    models = {card.get("distill_model") for card in cards}
    if len(paper_ids) != 1 or len(titles) != 1 or len(ownership) != 1 or len(models) != 1:
        raise RuntimeError("Capabilities_SEGMENT_CARD_IDENTITY_CONFLICT")

    fields: dict[str, Any] = {}
    by_field: dict[str, list[dict[str, Any]]] = {}
    for field in CONTENT_FIELDS:
        values = []
        anchors = []
        for card in cards:
            value = card.get(field)
            values.extend(value if isinstance(value, list) else [value])
            source = card.get("source_anchor")
            field_map = source.get("by_field") if isinstance(source, Mapping) else None
            if isinstance(field_map, Mapping) and isinstance(field_map.get(field), list):
                anchors.extend(field_map[field])
        clean = _dedupe([value for value in values if isinstance(value, str) and value.strip()])
        fields[field] = clean if field in LIST_FIELDS else "\n\n".join(clean)
        by_field[field] = _dedupe([item for item in anchors if isinstance(item, Mapping)])
        if not fields[field] or not by_field[field]:
            raise RuntimeError(f"Capabilities_SEGMENT_REQUIRED_FIELD_NOT_CLOSED:{field}")

    key_data = _dedupe(
        [item for card in cards for item in (card.get("key_data") or []) if isinstance(item, Mapping)]
    )
    aux_complete = all(
        all(key in card for key in ("comparison_context", "author_limitations_outlook", "terms", "citations"))
        for card in cards
    )
    aux = None
    if aux_complete:
        limitations = []
        outlook = []
        for card in cards:
            block = card.get("author_limitations_outlook") or {}
            if isinstance(block, Mapping):
                limitations.extend(block.get("limitations") or [])
                outlook.extend(block.get("outlook") or [])
        aux = {
            "comparison_context": _dedupe(
                [item for card in cards for item in (card.get("comparison_context") or [])]
            ),
            "author_limitations_outlook": {
                "limitations": _dedupe(limitations),
                "outlook": _dedupe(outlook),
            },
            "terms": _dedupe([item for card in cards for item in (card.get("terms") or [])]),
            "citations": _dedupe([item for card in cards for item in (card.get("citations") or [])]),
        }
    card = build_card(
        paper_id=next(iter(paper_ids)),
        title=next(iter(titles)),
        data_ownership=next(iter(ownership)),
        fields=fields,
        by_field=by_field,
        distill_model=next(iter(models)),
        distilled_at=now_minute(),
        schema_version=CARD_SCHEMA_VERSION,
        key_data=key_data,
        aux=aux,
    )
    return card, {
        "composition_mode": "SOURCE_ORDER_CONCAT_NO_NEW_SUMMARIZATION",
        "segment_card_count": len(cards),
        "aux_complete": aux_complete,
        "field_value_counts": {
            field: len(fields[field]) if isinstance(fields[field], list) else len(fields[field].split("\n\n"))
            for field in CONTENT_FIELDS
        },
        "field_anchor_counts": {field: len(by_field[field]) for field in CONTENT_FIELDS},
        "key_data_count": len(key_data),
    }


class CoreSegmentedDistiller:
    """Activate LONG-DOCUMENT capacity routing without changing Core's short path."""

    def __init__(self, *, workflow_config: Mapping[str, Any], evidence_root: Path | str, _split_depth: int = 0):
        self.workflow_config = deepcopy(dict(workflow_config))
        self.evidence_root = Path(evidence_root).resolve(strict=False)
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.last_metrics: dict[str, Any] = {}
        self._split_depth = _split_depth

    def distill(
        self,
        source: Path | str,
        *,
        target: str = "sandbox",
        overwrite: bool = False,
        legacy_distill: Callable[..., DistillResult] | None = None,
        comparison_mode: bool = False,
        force_segment: bool = False,
        _prior_truncation: DistillResult | None = None,
    ) -> DistillResult:
        if force_segment and not comparison_mode:
            raise RuntimeError("Capabilities_FORCE_SEGMENT_PRODUCTION_FORBIDDEN")
        if target != "sandbox":
            raise RuntimeError("Capabilities_SEGMENT_TARGET_MUST_BE_SANDBOX")
        if legacy_distill is None:
            from evidence_extraction import distill as legacy_distill

        folder, cleanmd, chunks_path = _locate(source)
        frontmatter = _read_frontmatter(cleanmd)
        records, ownership = _read_chunks(
            chunks_path, frontmatter["paper_id"], frontmatter["data_ownership"]
        )
        _ownership_gate(ownership, frontmatter["paper_id"])
        manifest = build_source_manifest(
            paper_id=frontmatter["paper_id"],
            chunks=_manifest_chunks(records),
            estimator_revision=ESTIMATOR_REVISION,
        )
        profile, profile_evidence = _resolve_capacity_profile(self.workflow_config)
        rendered_source_character_estimate = len(_render_chunks(records))
        character_route_triggered = (
            rendered_source_character_estimate > INPUT_MAX_CHARS
        )
        if profile is not None:
            decision = route_document(
                manifest,
                profile,
                force_segment=force_segment,
                comparison_mode=comparison_mode,
            )
            decision_route = decision.route
            decision_evidence = decision.as_dict()
        else:
            decision_route = (
                CapacityRoute.SEGMENTED_LONG_DOCUMENT
                if character_route_triggered or force_segment
                else CapacityRoute.LEGACY_SINGLE_PASS
            )
            decision_evidence = {
                "route": decision_route.value,
                "profile_id": None,
                "estimated_input_tokens": sum(
                    int(row["estimated_tokens"]) for row in manifest["chunks"]
                ),
                "safe_context_tokens": None,
                "reserved_tokens": None,
                "available_input_tokens": None,
                "reasons": [
                    (
                        "DOCUMENT_SIZE_ROUTE_SIGNAL"
                        if character_route_triggered
                        else (
                            "EXPLICIT_A_COMPARISON_FORCE_SEGMENT"
                            if force_segment
                            else "PROVIDER_MANAGED_SINGLE_PASS_WITH_LENGTH_HANDOFF"
                        )
                    )
                ],
                "force_segment_comparison": force_segment and comparison_mode,
                "product_hard_limit_added": False,
            }
        route_evidence = {
            "schema_version": "DesktopLongDocumentCapacityRoute-v1",
            "adapter_profile_revision": ADAPTER_PROFILE_REVISION,
            "profile": profile_evidence,
            "decision": decision_evidence,
            "source_manifest_hash": manifest["source_manifest_hash"],
            "source_chunk_count": len(records),
            "legacy_input_character_limit": INPUT_MAX_CHARS,
            "legacy_input_limit_role": "SEGMENTATION_ROUTE_SIGNAL_NOT_TERMINAL_LIMIT",
            "rendered_source_character_estimate": rendered_source_character_estimate,
            "comparison_mode": comparison_mode,
            "force_segment": force_segment,
        }
        _atomic_json(self.evidence_root / "capacity_route.json", route_evidence)
        _atomic_json(self.evidence_root / "source_manifest.json", manifest)
        if decision_route is CapacityRoute.BLOCKED_ROUTE_UNCERTAIN:
            raise RuntimeError(
                "Capabilities_SEGMENT_CAPACITY_BLOCKED:"
                + ",".join(str(value) for value in decision_evidence["reasons"])
            )
        effective_route = decision_route.value
        adaptive_fallback: dict[str, Any] | None = None
        fallback_path = self.evidence_root / 'adaptive_segment_fallback.json'
        fallback_identity = {'source_manifest_hash': manifest['source_manifest_hash'],
            'clean_sha256': _file_sha256(cleanmd), 'chunks_sha256': _file_sha256(chunks_path),
            'profile_hash': _sha256(profile_evidence)}
        if fallback_path.is_file():
            saved = json.loads(fallback_path.read_text('utf8'))
            if saved.get('input_identity') != fallback_identity:
                raise RuntimeError('Capabilities_SEGMENT_FALLBACK_REPLAY_IDENTITY_CONFLICT')
            adaptive_fallback = saved
        if _prior_truncation is not None:
            if not _is_output_truncation(_prior_truncation):
                raise ValueError('Capabilities_SEGMENT_TRUNCATION_HANDOFF_INVALID')
            adaptive_fallback = {'schema_version': 'DesktopAdaptiveSegmentFallback-v1',
                'trigger':'SEGMENT_OUTPUT_TRUNCATION', 'input_identity':fallback_identity,
                'additional_single_pass_retry_performed':False}
            _atomic_json(fallback_path, adaptive_fallback)
        if adaptive_fallback is not None:
            effective_route = 'SEGMENTED_AFTER_OUTPUT_TRUNCATION'
        if character_route_triggered:
            effective_route = "SEGMENTED_BY_DOCUMENT_SIZE_ROUTE_SIGNAL"
        if decision_route is CapacityRoute.LEGACY_SINGLE_PASS and not character_route_triggered and adaptive_fallback is None:
            card_path = folder / file_name(
                "Card", frontmatter["paper_id"], display_title(frontmatter["title"]), "md"
            )
            summary_path = self.evidence_root / "segmented_distill_summary.json"
            final_binding = {
                "source_manifest_hash": manifest["source_manifest_hash"],
                "clean_sha256": _file_sha256(cleanmd),
                "profile_hash": _sha256(profile_evidence),
            }
            if card_path.is_file() and not overwrite:
                expected_model = profile_evidence["model_name"]
                if summary_path.is_file():
                    prior_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    if "final_binding" in prior_summary:
                        if (prior_summary["final_binding"] != final_binding
                                or prior_summary.get("final_card_sha256") != _file_sha256(card_path)
                                or not prior_summary.get("final_card_model")):
                            raise RuntimeError("Capabilities_SEGMENT_FINAL_REPLAY_IDENTITY_CONFLICT")
                        # A provider may echo an alias. Accept only the exact
                        # previously completed output bound to this input/profile.
                        expected_model = prior_summary["final_card_model"]
                replay_card, self_check = _validate_replay_card(
                    card_path,
                    source_frontmatter=frontmatter,
                    expected_model=expected_model,
                )
                self.last_metrics = {
                    **route_evidence,
                    "route": decision_route.value,
                    "segment_count": 1,
                    "core_short_path_unchanged": True,
                    "recovered_existing_final_card": True,
                    "final_card_sha256": _file_sha256(card_path),
                    "final_card_model": replay_card["distill_model"],
                    "final_binding": final_binding,
                }
                _atomic_json(
                    self.evidence_root / "segmented_distill_summary.json",
                    self.last_metrics,
                )
                return DistillResult(
                    status="completed",
                    paper_id=frontmatter["paper_id"],
                    card_path=str(card_path),
                    distill_model=str(replay_card["distill_model"]),
                    distilled_at=str(replay_card["distilled_at"]),
                    chunk_count=len(records),
                    self_check=self_check,
                )
            legacy_parameters = signature(legacy_distill).parameters
            kwargs = {
                "target": target,
                "overwrite": overwrite,
            }
            if "max_tokens" in legacy_parameters and profile is not None:
                kwargs["max_tokens"] = profile.max_output_tokens
            if "segment_handoff_on_length" in legacy_parameters:
                kwargs["segment_handoff_on_length"] = True
            if "route_large_input" in legacy_parameters:
                kwargs["route_large_input"] = True
            result = legacy_distill(folder, **kwargs)
            error_text = f"{result.error_type or ''} {result.error_message or ''}"
            direct_segment_handoff = "segment_handoff" in error_text.lower()
            length_retry_exhausted = _is_output_truncation(result)
            if not length_retry_exhausted:
                self.last_metrics = {
                    **route_evidence,
                    "route": decision_route.value,
                    "segment_count": 1,
                    "core_short_path_unchanged": True,
                    "recovered_existing_final_card": False,
                }
                if result.status == "completed" and result.card_path:
                    self.last_metrics.update(
                        final_card_sha256=_file_sha256(Path(result.card_path)),
                        final_card_model=result.distill_model,
                        final_binding=final_binding,
                    )
                _atomic_json(
                    self.evidence_root / "segmented_distill_summary.json",
                    self.last_metrics,
                )
                return result
            effective_route = "SEGMENTED_AFTER_OUTPUT_TRUNCATION"
            adaptive_fallback = {
                "schema_version": "DesktopAdaptiveSegmentFallback-v1",
                "input_identity": fallback_identity,
                "trigger": (
                    "OUTPUT_TRUNCATION_DIRECT_SEGMENT_HANDOFF"
                    if direct_segment_handoff
                    else "OUTPUT_TRUNCATION_RETRY_EXHAUSTED"
                ),
                "legacy_result_status": result.status,
                "legacy_error_type": result.error_type,
                "original_capacity_route": decision_route.value,
                "effective_route": effective_route,
                "token_limits_changed": False,
                "additional_single_pass_retry_performed": False,
                "raw_error_message_persisted": False,
            }
            _atomic_json(
                self.evidence_root / "adaptive_segment_fallback.json",
                adaptive_fallback,
            )

        topology_core_limit = (
            profile.segment_core_token_limit
            if profile is not None
            else (
                INPUT_MAX_CHARS
                if character_route_triggered
                else max(
                    1,
                    math.ceil(
                        sum(
                            int(chunk["estimated_tokens"])
                            for chunk in manifest["chunks"]
                        )
                        / 2
                    ),
                )
            )
        )
        if comparison_mode and force_segment and len(manifest["chunks"]) > 1:
            # A forced comparison must actually yield a comparative segmented
            # topology even when the selected model has enough capacity for the
            # entire synthetic document.  This branch is forbidden in product
            # execution and therefore does not impose a production hard cap.
            topology_core_limit = min(
                topology_core_limit,
                max(int(chunk["estimated_tokens"]) for chunk in manifest["chunks"]),
            )
        serialized_limit = min(INPUT_MAX_CHARS, topology_core_limit)
        if adaptive_fallback is not None:
            # A truncation handoff must reduce real input, even for models whose
            # advertised context could hold the entire document in one call.
            serialized_limit = min(serialized_limit, max(1, rendered_source_character_estimate // 2))
        topology = _executable_topology(records, manifest,
            min(topology_core_limit, serialized_limit), serialized_limit)
        _atomic_json(self.evidence_root / "topology.json", topology)
        validator = SegmentShardValidator(manifest, topology)
        ledger = SegmentCompletionLedger(
            topology,
            max_attempts_per_segment=2,
            max_total_calls=max(1, len(topology["segments"]) * 2),
        )
        cards = []
        segment_receipts = []
        for segment in topology["segments"]:
            sequence = int(segment["sequence_index"])
            segment_root = self.evidence_root / "segments" / f"{sequence:03d}_{segment['segment_id']}"
            paper_root = segment_root / "paper"
            paper_root.mkdir(parents=True, exist_ok=True)
            segment_rows = _segment_records(records, manifest, segment)
            segment_clean = paper_root / cleanmd.name
            segment_chunks = paper_root / chunks_path.name
            _atomic_text(segment_clean, cleanmd.read_text(encoding="utf-8"))
            _atomic_text(
                segment_chunks,
                "".join(
                    json.dumps(row, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
                    for row in segment_rows
                ),
            )
            input_identity = {
                "source_manifest_hash": manifest["source_manifest_hash"],
                "topology_hash": topology["topology_hash"],
                "segment_id": segment["segment_id"],
                "execution_profile_hash": _sha256(self.workflow_config["nodes"]["03_CARD_DISTILL"]),
                "clean_sha256": _file_sha256(segment_clean),
                "chunks_sha256": _file_sha256(segment_chunks),
            }
            receipt_path = segment_root / "segment_receipt.json"
            existing_cards = sorted(paper_root.glob("[[]Card[]]*.md"))
            if receipt_path.is_file() and len(existing_cards) == 1:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("input_identity") != input_identity or receipt.get("card_sha256") != _file_sha256(existing_cards[0]):
                    raise RuntimeError(f"Capabilities_SEGMENT_REPLAY_CONFLICT:{segment['segment_id']}")
                card_path = existing_cards[0]
            else:
                if existing_cards or receipt_path.exists():
                    raise RuntimeError(f"Capabilities_SEGMENT_PARTIAL_REPLAY_STATE:{segment['segment_id']}")
                segment_parameters = signature(legacy_distill).parameters
                segment_kwargs = {"target": target, "overwrite": False}
                if "max_tokens" in segment_parameters and profile is not None:
                    segment_kwargs["max_tokens"] = profile.max_output_tokens
                if "route_large_input" in segment_parameters:
                    segment_kwargs["route_large_input"] = True
                if "segment_handoff_on_length" in segment_parameters:
                    segment_kwargs["segment_handoff_on_length"] = True
                child_root = segment_root/'subsegments'
                if (child_root/'adaptive_segment_fallback.json').is_file():
                    result = CoreSegmentedDistiller(workflow_config=self.workflow_config,
                        evidence_root=child_root, _split_depth=self._split_depth+1).distill(
                            paper_root, target=target, legacy_distill=legacy_distill)
                else:
                    result = legacy_distill(paper_root, **segment_kwargs)
                if _is_output_truncation(result):
                    if self._split_depth >= topology['max_split_depth']:
                        raise RuntimeError('Capabilities_SEGMENT_CAPACITY_UNRESOLVED:MAX_SPLIT_DEPTH')
                    # Reuse this adapter on the failed segment only. The prior
                    # response becomes its persisted handoff; never send that
                    # unchanged segment again before dividing it.
                    child = CoreSegmentedDistiller(workflow_config=self.workflow_config,
                        evidence_root=child_root, _split_depth=self._split_depth+1)
                    result = child.distill(paper_root, target=target,
                        legacy_distill=legacy_distill, _prior_truncation=result)
                if result.status != "completed" or not result.card_path:
                    raise RuntimeError(
                        f"Capabilities_SEGMENT_Core_DISTILL_FAILED:{segment['segment_id']}:{result.error_type}:{result.error_message}"
                    )
                card_path = Path(result.card_path)
                receipt = {
                    "schema_version": "DesktopLongDocumentSegmentReceipt-v1",
                    "input_identity": input_identity,
                    "card_sha256": _file_sha256(card_path),
                    "distill_model": result.distill_model,
                    "chunk_ids": [row["chunk_id"] for row in segment_rows],
                    "producer": "Core_EVIDENCE_EXTRACTION_DISTILL_REUSED",
                }
                _atomic_json(receipt_path, receipt)
            raw_shard = {
                "schema_version": "long_document-segment-fact-shard-v1",
                "artifact_kind": "segment_fact_shard",
                "paper_id": manifest["paper_id"],
                "segment_id": segment["segment_id"],
                "attempt_id": f"core-card-{sequence:03d}",
                "source_manifest_hash": manifest["source_manifest_hash"],
                "topology_hash": topology["topology_hash"],
                "expected_core_chunk_ids": segment["core_chunk_ids"],
                "observed_source_ids": [],
                "terminal_marker": "LONG_DOCUMENT_SEGMENT_COMPLETE",
                "facts": [],
                "slot_observations": [],
                "high_risk_tokens": [],
                "local_conflicts": [],
                "capacity_state": "complete",
                "producer_receipt_ref": f"sha256:{receipt['card_sha256']}",
            }
            shard = validator.validate(raw_shard, provider_receipt={"finish_reason": "stop"})
            ledger.record_attempt(
                segment_id=segment["segment_id"],
                attempt_id=raw_shard["attempt_id"],
                state="terminal",
                receipt_ref=raw_shard["producer_receipt_ref"],
            )
            ledger.admit(shard)
            cards.append(_frontmatter(card_path))
            segment_receipts.append(receipt)

        completion = ledger.snapshot()
        _atomic_json(self.evidence_root / "segment_completion_ledger.json", completion)
        if completion["recovery_state"] != "completed" or completion["missing_segment_ids"]:
            raise RuntimeError("Capabilities_SEGMENT_COMPLETION_BARRIER_NOT_CLOSED")
        final_card, aggregation = _aggregate_cards(cards)
        card_path = folder / file_name(
            "Card", frontmatter["paper_id"], display_title(frontmatter["title"]), "md"
        )
        recovered_existing_final_card = False
        if card_path.is_file() and not overwrite:
            replay_card, self_check = _validate_replay_card(
                card_path,
                source_frontmatter=frontmatter,
                # Reconstructed from exact, profile-bound segment receipts.
                # Provider echoes may differ from the requested model ID.
                expected_model=str(final_card["distill_model"]),
            )
            if _semantic_card_projection(replay_card) != _semantic_card_projection(final_card):
                raise RuntimeError("Capabilities_SEGMENT_FINAL_REPLAY_CONTENT_CONFLICT")
            final_card = replay_card
            recovered_existing_final_card = True
        else:
            write_card(card_path, final_card, overwrite=overwrite)
            card_lang = detect_card_lang([final_card.get(field) for field in CONTENT_FIELDS])
            self_check = _self_check(final_card, card_lang)
        assembly = {
            "schema_version": "DesktopLongDocumentCoreCardAssembly-v1",
            "adapter_profile_revision": ADAPTER_PROFILE_REVISION,
            "source_manifest_hash": manifest["source_manifest_hash"],
            "topology_hash": topology["topology_hash"],
            "completion_ledger_hash": completion["content_hash"],
            "segment_card_sha256s": [receipt["card_sha256"] for receipt in segment_receipts],
            "final_card_sha256": _file_sha256(card_path),
            **aggregation,
        }
        _atomic_json(self.evidence_root / "card_assembly.json", assembly)
        self.last_metrics = {
            **route_evidence,
            "route": effective_route,
            "segment_count": len(topology["segments"]),
            "core_short_path_unchanged": False,
            "completion_state": completion["recovery_state"],
            "recovered_existing_final_card": recovered_existing_final_card,
            "assembly": assembly,
        }
        if adaptive_fallback is not None:
            self.last_metrics["adaptive_fallback"] = adaptive_fallback
        _atomic_json(self.evidence_root / "segmented_distill_summary.json", self.last_metrics)
        return DistillResult(
            status="completed",
            paper_id=frontmatter["paper_id"],
            card_path=str(card_path),
            distill_model=final_card["distill_model"],
            distilled_at=str(final_card["distilled_at"]),
            chunk_count=len(records),
            self_check=self_check,
        )


__all__ = [
    "ADAPTER_PROFILE_REVISION",
    "ESTIMATOR_REVISION",
    "CoreSegmentedDistiller",
]
