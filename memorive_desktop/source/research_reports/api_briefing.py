"""API narrative layer for deterministic RESEARCH_REPORTS daily/weekly/monthly digests.

The ChangeDigest remains the sole fact and window authority.  This module adds
one period-specific, source-bound front narrative only when that exact window
contains a qualifying library-state change.  No qualifying change means no
report and zero model calls; incomplete source authority blocks before send.

Provider transport, credentials, retry policy and strict per-call receipts are
injected through ``model_call`` and remain MODEL_GATEWAY/RUNTIME_LOG responsibilities.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import jsonschema

from model_gateway.prompts import PromptRegistry

from .renderer import render_markdown_with_narrative


GENERATED = "GENERATED"
SKIPPED_NO_LIBRARY_CHANGE = "SKIPPED_NO_LIBRARY_CHANGE"
BLOCKED_INPUT_INCOMPLETE = "BLOCKED_INPUT_INCOMPLETE"

NARRATIVE_VERSION = "memorive.research.research_reports.brief-narrative.v1"
_PROMPTS = PromptRegistry(Path(__file__).resolve().parents[1] / "model_gateway" / "prompts")
_SUCCESS_STOPS = frozenset({"stop", "end_turn"})
_LIBRARY_CHANGE_AXES = frozenset(
    {
        "review_status",
        "artifact_review_status",
        "artifact_location",
        "artifact_lineage",
        "artifact_registry",
    }
)


class BriefingApiError(RuntimeError):
    """A brief cannot be generated safely from the supplied digest/call."""


@dataclass(frozen=True)
class BriefProfile:
    slot: str
    provider: str
    model: str
    thinking: dict[str, str]
    effort: str | None
    max_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class ApiBriefingResult:
    status: str
    digest_id: str
    digest_kind: str
    slot: str | None
    qualifying_change_ids: tuple[str, ...]
    model_call_count: int
    narrative: dict[str, Any] | None
    markdown_text: str | None
    markdown_sha256: str | None
    model_metadata: dict[str, Any] | None
    reason_codes: tuple[str, ...]


PROFILES = {
    "daily": BriefProfile(
        slot="report_daily",
        provider="deepseek",
        model="deepseek-v4-flash",
        thinking={"type": "disabled"},
        effort=None,
        max_tokens=4096,
        timeout_seconds=300,
    ),
    "weekly": BriefProfile(
        slot="report_weekly",
        provider="deepseek",
        model="deepseek-v4-pro",
        thinking={"type": "enabled"},
        effort="high",
        max_tokens=8192,
        timeout_seconds=600,
    ),
    "monthly": BriefProfile(
        slot="report_monthly",
        provider="anthropic",
        model="claude-sonnet-5",
        thinking={"type": "adaptive"},
        effort="max",
        max_tokens=65536,
        timeout_seconds=1200,
    ),
}

BriefModelCall = Callable[[str, dict[str, Any]], Mapping[str, Any]]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _safe_digest(digest: Any) -> dict[str, Any]:
    if not isinstance(digest, Mapping):
        raise BriefingApiError("DIGEST_NOT_OBJECT")
    value = copy.deepcopy(dict(digest))
    kind = value.get("digest_kind")
    if kind not in PROFILES:
        raise BriefingApiError("DIGEST_KIND_UNSUPPORTED")
    if not isinstance(value.get("digest_id"), str) or not value["digest_id"]:
        raise BriefingApiError("DIGEST_ID_INVALID")
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise BriefingApiError("DIGEST_SECTIONS_INVALID")
    for index, section in enumerate(sections):
        if not isinstance(section, Mapping) or not isinstance(section.get("title"), str):
            raise BriefingApiError(f"DIGEST_SECTION_INVALID:{index}")
        if not isinstance(section.get("items"), list):
            raise BriefingApiError(f"DIGEST_SECTION_ITEMS_INVALID:{index}")
    if not isinstance(value.get("late_arrivals"), list):
        raise BriefingApiError("DIGEST_LATE_ARRIVALS_INVALID")
    return value


def _event_map(digest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    values = [
        item
        for section in digest["sections"]
        for item in section["items"]
    ] + list(digest["late_arrivals"])
    for item in values:
        if not isinstance(item, Mapping):
            raise BriefingApiError("DIGEST_EVENT_NOT_OBJECT")
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise BriefingApiError("DIGEST_EVENT_ID_INVALID")
        normalized = copy.deepcopy(dict(item))
        prior = result.get(event_id)
        if prior is not None and prior != normalized:
            raise BriefingApiError("DIGEST_DUPLICATE_EVENT_MISMATCH")
        result[event_id] = normalized
    return result


def qualifying_library_change_ids(digest: Mapping[str, Any]) -> tuple[str, ...]:
    """Return exact event IDs that prove a library/registry state change.

    RUNTIME_LOG attempt/run/publication telemetry is report context but does not by
    itself prove that the literature library changed.  KNOWLEDGE_ADMISSION live review-state
    changes and ARTIFACT_REGISTRY artifact registry/location/lineage/review changes do.
    """

    value = _safe_digest(digest)
    events = _event_map(value)
    return tuple(
        sorted(
            event_id
            for event_id, item in events.items()
            if item.get("status_axis") in _LIBRARY_CHANGE_AXES
        )
    )


def _render_prompt(kind: str, contract: Mapping[str, Any], schema=None) -> str:
    module = f"report_{kind}"
    try:
        body = _PROMPTS.get(module).body
    except (KeyError, OSError) as exc:
        raise BriefingApiError(f"REPORT_PROMPT_UNAVAILABLE:{kind}") from exc
    replacements = {
        "__BRIEF_INPUT_JSON__": contract,
        "__BRIEF_OUTPUT_SCHEMA_JSON__": NARRATIVE_SCHEMA if schema is None else schema,
    }
    rendered = body
    for marker, value in replacements.items():
        if rendered.count(marker) != 1:
            raise BriefingApiError(f"REPORT_PROMPT_MARKER_INVALID:{kind}:{marker}")
        rendered = rendered.replace(
            marker,
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    if "__BRIEF_" in rendered:
        raise BriefingApiError(f"REPORT_PROMPT_UNRESOLVED_MARKER:{kind}")
    return rendered


def _payload(
    digest: dict[str, Any],
    allowed_event_ids: tuple[str, ...],
    profile: BriefProfile,
) -> dict[str, Any]:
    contract = {
        "schema_version": "memorive.research.research_reports.brief-input.v1",
        "digest": copy.deepcopy(digest),
        "allowed_event_ids": sorted(_event_map(digest)),
        "qualifying_library_change_ids": list(allowed_event_ids),
        "narrative_boundary": {
            "fact_authority": "ChangeDigest only",
            "model_may_change_counts_hashes_or_status": False,
            "model_may_make_scientific_judgment": False,
            "details_remain_deterministic": True,
        },
    }
    prompt = _render_prompt(digest["digest_kind"], contract)
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "thinking": copy.deepcopy(profile.thinking),
        "max_tokens": profile.max_tokens,
        "stream": True,
        "timeout": profile.timeout_seconds,
        "wall_clock_deadline_seconds": profile.timeout_seconds,
        "memorive_contract": {
            **copy.deepcopy(contract),
            "schema": copy.deepcopy(NARRATIVE_SCHEMA),
            "profile": {
                "slot": profile.slot,
                "provider": profile.provider,
                "model": profile.model,
                "thinking": copy.deepcopy(profile.thinking),
                "effort": profile.effort,
                "max_tokens": profile.max_tokens,
            },
            "prompt_sha256": _hash_text(prompt),
        },
    }
    if profile.provider == "deepseek":
        payload["response_format"] = {"type": "json_object"}
        if profile.effort is not None:
            payload["reasoning_effort"] = profile.effort
    elif profile.provider == "anthropic":
        if profile.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise BriefingApiError("ANTHROPIC_BRIEF_EFFORT_INVALID")
        payload["effort"] = profile.effort
        # Keep the nested narrative schema in the prompt and validate locally;
        # native grammar compilation may reject this complex shape before the
        # model is invoked.
        payload["output_config"] = {"effort": profile.effort}
        payload["memorive_contract"]["structured_output_mode"] = (
            "PROMPT_JSON_PLUS_LOCAL_SCHEMA"
        )
    else:
        raise BriefingApiError("BRIEF_PROVIDER_UNSUPPORTED")
    return payload


def _parse_json(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise BriefingApiError("MODEL_OUTPUT_TEXT_MISSING")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BriefingApiError("MODEL_OUTPUT_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise BriefingApiError("MODEL_OUTPUT_NOT_OBJECT")
    return value


def _call(
    *,
    profile: BriefProfile,
    payload: dict[str, Any],
    model_call: BriefModelCall,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = model_call(profile.slot, payload)
    if not isinstance(response, Mapping):
        raise BriefingApiError("MODEL_RESPONSE_NOT_OBJECT")
    if response.get("provider") != profile.provider:
        raise BriefingApiError("RETURNED_PROVIDER_MISMATCH")
    if response.get("model") != profile.model:
        raise BriefingApiError("RETURNED_MODEL_MISMATCH")
    if response.get("stop_reason") not in _SUCCESS_STOPS:
        raise BriefingApiError("MODEL_STOP_REASON_NOT_COMPLETE")
    transport = response.get("transport")
    if not isinstance(transport, Mapping) or transport.get("complete") is not True:
        raise BriefingApiError("MODEL_TRANSPORT_INCOMPLETE")
    if transport.get("stream") is not True:
        raise BriefingApiError("MODEL_STREAM_MODE_MISMATCH")
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise BriefingApiError("MODEL_USAGE_MISSING")
    raw_text = response.get("text")
    value = _parse_json(raw_text)
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    metadata = {
        "slot": profile.slot,
        "provider": profile.provider,
        "model": profile.model,
        "response_sha256": _hash_text(raw_text),
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", "NOT_RECORDED")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", "NOT_RECORDED")),
        "reasoning_tokens": details.get("reasoning_tokens", "NOT_RECORDED"),
    }
    return value, metadata


def _validate_narrative(
    raw: Any,
    digest: dict[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(raw)
    if isinstance(value, dict):
        aliases = _event_aliases(digest)
        for key in ("period_summary", "priority_items", "section_summaries"):
            rows = value.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("event_ids"), list):
                    row["event_ids"] = [aliases.get(ref, ref) if isinstance(ref, str) else ref
                                        for ref in row["event_ids"]]
        # A canonical absence marker contains no claim to retain or infer.
        # Unanchored prose, unknown references, and malformed rows still fail.
        if isinstance(value.get("priority_items"), list):
            value["priority_items"] = [row for row in value["priority_items"]
                                      if row != {"text": "NONE", "event_ids": []}]
    try:
        jsonschema.Draft202012Validator(NARRATIVE_SCHEMA).validate(value)
    except jsonschema.ValidationError as exc:
        raise BriefingApiError("NARRATIVE_SCHEMA_INVALID") from exc
    value = dict(value)
    if value["digest_id"] != digest["digest_id"] or value["digest_kind"] != digest["digest_kind"]:
        raise BriefingApiError("NARRATIVE_DIGEST_BINDING_MISMATCH")
    event_ids = set(_event_map(digest))

    def validate_rows(rows: list[dict[str, Any]], *, allow_empty: bool = False) -> None:
        if not rows and not allow_empty:
            raise BriefingApiError("NARRATIVE_REQUIRED_ROWS_EMPTY")
        for row in rows:
            if not set(row["event_ids"]).issubset(event_ids):
                raise BriefingApiError("NARRATIVE_EVENT_REF_UNKNOWN")

    validate_rows(value["period_summary"])
    validate_rows(value["priority_items"], allow_empty=True)
    if len(value["section_summaries"]) != len(digest["sections"]):
        raise BriefingApiError("NARRATIVE_SECTION_COVERAGE_INVALID")
    for section, row in zip(digest["sections"], value["section_summaries"], strict=True):
        # Section order and identity belong to the frozen digest, not to the
        # narrative model.  Reattach the exact runtime title so harmless forms
        # such as "S01-title" cannot consume another paid call.
        row["section_title"] = section["title"]
        allowed = {item["event_id"] for item in section["items"]}
        if not set(row["event_ids"]).issubset(allowed):
            raise BriefingApiError("NARRATIVE_SECTION_EVENT_REF_INVALID")
        if allowed:
            if row["text"] == "NONE" or not row["event_ids"]:
                raise BriefingApiError("NARRATIVE_NONEMPTY_SECTION_SUMMARY_INVALID")
        else:
            if row["event_ids"]:
                raise BriefingApiError("NARRATIVE_EMPTY_SECTION_SUMMARY_INVALID")
            # An empty frozen section has no source material to narrate.  Its
            # only valid canonical representation is therefore deterministic.
            row["text"] = "NONE"
    return value


def _event_aliases(digest):
    """Closed, deterministic aliases; never reinterpret a real event ID."""
    ids = sorted(_event_map(digest))
    prefix = "E"
    while True:
        aliases = {f"{prefix}{index:03d}": event_id for index, event_id in enumerate(ids, 1)}
        if set(aliases).isdisjoint(ids):
            return aliases
        prefix += "_"


def prepare_mapped_briefing(digest,language=None):
    """Provider-neutral input for desktop's explicitly selected execution profile."""
    value = _safe_digest(digest)
    if value.get('report_status') != 'complete' or value.get('conflicts'):
        raise BriefingApiError('REPORT_INPUT_INCOMPLETE')
    if not qualifying_library_change_ids(value):
        raise BriefingApiError('REPORT_NO_LIBRARY_CHANGE')
    aliases = _event_aliases(value)
    inverse = {event_id: alias for alias, event_id in aliases.items()}
    projection = copy.deepcopy(value)
    for section in projection['sections']:
        for row in section['items']:
            row['event_id'] = inverse[row['event_id']]
    for row in projection['late_arrivals']:
        row['event_id'] = inverse[row['event_id']]
    schema = copy.deepcopy(NARRATIVE_SCHEMA)
    for key in ('period_summary', 'priority_items', 'section_summaries'):
        schema['properties'][key]['items']['properties']['event_ids']['items']['enum'] = list(aliases)
    contract = {
        'schema_version': 'memorive.research.research_reports.brief-input.v1', 'digest': projection,
        'event_reference_mode': 'LOCAL_EXACT_ALIAS_V1',
        'allowed_event_ids': list(aliases),
        'qualifying_library_change_ids': [inverse[event_id] for event_id in qualifying_library_change_ids(value)],
        'narrative_boundary': {'fact_authority': 'ChangeDigest only',
            'model_may_change_counts_hashes_or_status': False,
            'model_may_make_scientific_judgment': False, 'details_remain_deterministic': True},
    }
    from memorive_language import validate
    if language is not None:contract['language_context']=validate(language)
    prompt=_render_prompt(value['digest_kind'], contract, schema)
    if language is not None:prompt+='\nFollow language_context.instruction for narrative prose. Section_title values are fixed schema labels; keep them unchanged.'
    return prompt, schema


def validate_mapped_narrative(raw, digest):
    return _validate_narrative(raw, _safe_digest(digest))


def run_api_briefing(
    *,
    digest: Mapping[str, Any],
    model_call: BriefModelCall | None = None,
    profile_override: BriefProfile | None = None,
) -> ApiBriefingResult:
    """Generate one due period brief, or return a zero-call skip/block receipt.

    ``profile_override`` is an explicit governed-run seam.  Normal production
    calls omit it and retain the period-specific defaults in ``PROFILES``.
    """

    value = _safe_digest(digest)
    kind = value["digest_kind"]
    digest_id = value["digest_id"]
    qualifying = qualifying_library_change_ids(value)
    if value.get("report_status") != "complete" or value.get("conflicts"):
        return ApiBriefingResult(
            status=BLOCKED_INPUT_INCOMPLETE,
            digest_id=digest_id,
            digest_kind=kind,
            slot=None,
            qualifying_change_ids=qualifying,
            model_call_count=0,
            narrative=None,
            markdown_text=None,
            markdown_sha256=None,
            model_metadata=None,
            reason_codes=("CHANGE_STATUS_NOT_COMPLETE",),
        )
    if not qualifying:
        return ApiBriefingResult(
            status=SKIPPED_NO_LIBRARY_CHANGE,
            digest_id=digest_id,
            digest_kind=kind,
            slot=None,
            qualifying_change_ids=(),
            model_call_count=0,
            narrative=None,
            markdown_text=None,
            markdown_sha256=None,
            model_metadata=None,
            reason_codes=("NO_QUALIFYING_LIBRARY_CHANGE_IN_WINDOW",),
        )
    if model_call is None:
        raise BriefingApiError("STRICT_MODEL_CALL_BINDING_REQUIRED")
    if profile_override is not None and not isinstance(profile_override, BriefProfile):
        raise BriefingApiError("BRIEF_PROFILE_OVERRIDE_INVALID")
    profile = profile_override or PROFILES[kind]
    payload = _payload(value, qualifying, profile)
    raw, metadata = _call(profile=profile, payload=payload, model_call=model_call)
    narrative = _validate_narrative(raw, value)
    markdown = render_markdown_with_narrative(value, narrative, metadata)
    return ApiBriefingResult(
        status=GENERATED,
        digest_id=digest_id,
        digest_kind=kind,
        slot=profile.slot,
        qualifying_change_ids=qualifying,
        model_call_count=1,
        narrative=narrative,
        markdown_text=markdown,
        markdown_sha256=_hash_text(markdown),
        model_metadata=metadata,
        reason_codes=("QUALIFYING_LIBRARY_CHANGE_PRESENT", "NARRATIVE_SCHEMA_AND_REF_PASS"),
    )


_NARRATIVE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "event_ids"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "event_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 3},
        },
    },
}

NARRATIVE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "digest_id", "digest_kind", "period_summary",
        "priority_items", "section_summaries"
    ],
    "properties": {
        "schema_version": {"const": NARRATIVE_VERSION},
        "digest_id": {"type": "string", "minLength": 3},
        "digest_kind": {"enum": ["daily", "weekly", "monthly"]},
        "period_summary": {
            "type": "array", "minItems": 1, "items": _NARRATIVE_ITEM
        },
        "priority_items": {
            "type": "array", "items": _NARRATIVE_ITEM
        },
        "section_summaries": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["section_title", "text", "event_ids"],
                "properties": {
                    "section_title": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                    "event_ids": {
                        "type": "array", "uniqueItems": True,
                        "items": {"type": "string", "minLength": 3},
                    },
                },
            },
        },
    },
}


__all__ = [
    "GENERATED", "SKIPPED_NO_LIBRARY_CHANGE", "BLOCKED_INPUT_INCOMPLETE",
    "BriefingApiError", "BriefProfile", "ApiBriefingResult", "PROFILES",
    "NARRATIVE_SCHEMA", "qualifying_library_change_ids", "run_api_briefing",
]
