"""RECOMMENDATION-HISTORY sandbox candidate: append-only exposure feedback and weak-signal guard.

The module is deliberately pure and offline.  It does not open files, read a
production adapter, use wall-clock time, access a network, or mutate Research,
Profile, Policy, DirectionRegistry, ARTIFACT_REGISTRY, or a formal library.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


Research_INTERACTION_MODES = ("FORMAL_CONTRACT_BOUND", "NOT_CONFIGURED")
Research_ORIGINS = (
    "USER_INITIATED",
    "Discovery_EXPOSURE_FOLLOWUP",
    "SYSTEM_CLARIFICATION",
    "RETRY_OR_REPHRASE",
)
CONTROL_EVENT_TYPES = ("DECISION_REVOKED", "FEEDBACK_RESET", "MANUAL_RESTORE")
DECISION_TYPES = (
    "NOT_FOR_THIS_DIRECTION",
    "GLOBALLY_USELESS",
    "ALREADY_READ",
    "IN_LIBRARY",
    "SNOOZE",
    "NO_LEGAL_FULLTEXT",
    "CARD_ERROR",
    "MANIFESTATION_NOT_USEFUL",
)
IGNORED_INTERACTIONS = ("NO_CLICK", "TIMEOUT", "NO_DECISION")
ALLOWED_RESURFACE_TRIGGERS = (
    "FORMAL_MAJOR_VERSION",
    "LEGAL_ACCESS_AVAILABLE",
    "CORRECTION_OR_RETRACTION",
    "IMPORTANT_CITATION_CHANGE",
    "NEW_STABLE_DIRECTION",
    "BRIDGE_ROLE_CHANGE",
    "MANUAL_RESTORE",
)
DIRECTION_TRANSITIONS = ("NONE", "SPLIT", "MERGE", "MAJOR_REVISION")
FORBIDDEN_PAYLOAD_KEYS = {
    "raw_query",
    "query_text",
    "query_body",
    "user_id",
    "user_name",
    "private_payload",
    "full_session",
    "session_text",
    "pdf_bytes",
    "fulltext",
    "answer_key",
    "nonce",
    "secret",
}
ZERO_SIDE_EFFECTS = {
    "network_calls": 0,
    "external_source_calls": 0,
    "model_or_api_calls": 0,
    "profile_writes": 0,
    "policy_writes": 0,
    "direction_writes": 0,
    "research_reads": 0,
    "research_writes": 0,
    "library_writes": 0,
    "artifact_registry_writes": 0,
    "production_reads": 0,
    "production_writes": 0,
}

FEEDBACK_TAXONOMY: dict[str, dict[str, Any]] = {
    "NOT_FOR_THIS_DIRECTION": {
        "route": "SCOPED_DIRECTION_SUPPRESSION",
        "scope": "work_cluster_id+direction_id+decision_revision",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "GLOBALLY_USELESS": {
        "route": "GLOBAL_EXCLUSION",
        "scope": "work_cluster_id",
        "negative_interest_signal": False,
        "confirmation": "SECOND_EXPLICIT_CONFIRMATION_REQUIRED",
    },
    "ALREADY_READ": {
        "route": "GLOBAL_DEDUPE",
        "scope": "work_cluster_id",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "IN_LIBRARY": {
        "route": "GLOBAL_DEDUPE",
        "scope": "work_cluster_id",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "SNOOZE": {
        "route": "TIME_BOUND_SNOOZE",
        "scope": "exposure_id",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "NO_LEGAL_FULLTEXT": {
        "route": "ACCESS_CHANGE_WAIT",
        "scope": "work_cluster_id+manifestation_id",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "CARD_ERROR": {
        "route": "CARD_REPAIR",
        "scope": "candidate_card_ref",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
    "MANIFESTATION_NOT_USEFUL": {
        "route": "MANIFESTATION_SCOPE_SUPPRESSION",
        "scope": "work_cluster_id+manifestation_id",
        "negative_interest_signal": False,
        "confirmation": "EXPLICIT_DECISION",
    },
}

BEHAVIOR_POLICY = {
    "dedupe_scope": "direction_id+session_hash",
    "origin_weights": {
        "USER_INITIATED": "1.000000",
        "Discovery_EXPOSURE_FOLLOWUP": "0.250000",
        "SYSTEM_CLARIFICATION": "0.000000",
        "RETRY_OR_REPHRASE": "0.000000",
    },
    "decay_windows_days": [
        {"max_age_days": 30, "factor": "1.000000"},
        {"max_age_days": 90, "factor": "0.500000"},
        {"max_age_days": 180, "factor": "0.250000"},
    ],
    "older_factor": "0.000000",
    "per_direction_cap": "1.000000",
    "proposal_min_user_sessions": 3,
    "proposal_min_active_dates": 2,
    "tie_break_scope": "SAME_CHANNEL+IDENTICAL_PRE_TIE_RANK_KEY",
    "epsilon": "0.000000",
    "global_scalar_score": False,
}

RESURFACE_POLICY = {
    "allowed_triggers": list(ALLOWED_RESURFACE_TRIGGERS),
    "forbidden_triggers": [
        "TITLE_DRIFT",
        "ABSTRACT_FORMAT_DRIFT",
        "SOURCE_MIRROR_CHANGE",
        "MINOR_METADATA_DRIFT",
    ],
    "authority_evidence_required": True,
    "history_disclosure_required": True,
    "manual_restore_is_append_only": True,
}


class ExposureFeedbackError(ValueError):
    """Fail-closed Verification candidate error carrying a stable reason code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest().upper()


def stable_id(prefix: str, value: Any) -> str:
    """Return the Memorive object-ID convention: prefix plus lowercase 24-hex."""

    return prefix + sha256_json(value)[:24].lower()


def seal_object(value: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(value))
    body.pop("content_hash", None)
    body["content_hash"] = sha256_json(body)
    return body


def assert_sealed(value: Mapping[str, Any], code: str) -> None:
    body = copy.deepcopy(dict(value))
    recorded = body.pop("content_hash", None)
    if not isinstance(recorded, str) or recorded != sha256_json(body):
        raise ExposureFeedbackError(code, "content_hash mismatch")


def _parse_time(value: str, code: str = "TIMESTAMP_INVALID") -> datetime:
    if not isinstance(value, str) or not value:
        raise ExposureFeedbackError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExposureFeedbackError(code, value) from exc
    if parsed.tzinfo is None:
        raise ExposureFeedbackError(code, "timezone required")
    return parsed.astimezone(timezone.utc)


def _require_sha256(value: Any, code: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789ABCDEF" for character in value)
    ):
        raise ExposureFeedbackError(code)


def _require_positive_integer(value: Any, code: str) -> None:
    if type(value) is not int or value < 1:
        raise ExposureFeedbackError(code)


def _scan_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_PAYLOAD_KEYS:
                raise ExposureFeedbackError("FORBIDDEN_PRIVATE_OR_RAW_FIELD", f"{path}.{key}")
            _scan_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _scan_forbidden_keys(child, f"{path}[{index}]")


def assert_business_io_forbidden(operation: str) -> None:
    codes = {
        "NETWORK": "NETWORK_FORBIDDEN",
        "EXTERNAL_SOURCE": "EXTERNAL_SOURCE_FORBIDDEN",
        "MODEL_OR_API": "MODEL_OR_API_FORBIDDEN",
        "REAL_Research_READ": "Research_REAL_ADAPTER_NOT_CONFIGURED",
        "PROFILE_READ": "PRODUCTION_PROFILE_READ_FORBIDDEN",
        "PROFILE_WRITE": "PRODUCTION_PROFILE_WRITE_FORBIDDEN",
        "POLICY_WRITE": "PRODUCTION_POLICY_WRITE_FORBIDDEN",
        "ARTIFACT_REGISTRY_WRITE": "ARTIFACT_REGISTRY_WRITE_FORBIDDEN",
        "FORMAL_LIBRARY_WRITE": "FORMAL_LIBRARY_WRITE_FORBIDDEN",
        "PRODUCTION_WRITE": "PRODUCTION_WRITE_FORBIDDEN",
    }
    if operation not in codes:
        raise ExposureFeedbackError("UNKNOWN_BUSINESS_IO_OPERATION", operation)
    raise ExposureFeedbackError(codes[operation])


def feedback_taxonomy_object() -> dict[str, Any]:
    return seal_object(
        {
            "schema_version": "discovery.verification.feedback-taxonomy.0.1",
            "object_type": "FeedbackTaxonomy",
            "taxonomy_revision": "Verification_FEEDBACK_TAXONOMY:r0.1",
            "reasons": [
                {"reason_code": code, **copy.deepcopy(FEEDBACK_TAXONOMY[code])}
                for code in sorted(FEEDBACK_TAXONOMY)
            ],
            "ignored_interactions": list(IGNORED_INTERACTIONS),
            "fuzzy_direction_propagation": False,
            "field_id_direction_substitution": False,
            "no_click_negative_feedback": False,
        }
    )


def resurface_policy_object() -> dict[str, Any]:
    return seal_object(
        {
            "schema_version": "discovery.verification.resurface-policy.0.1",
            "object_type": "ResurfacePolicy",
            "policy_revision": "Verification_RESURFACE_POLICY:r0.1",
            **copy.deepcopy(RESURFACE_POLICY),
        }
    )


def validate_slate_context(
    slate: Mapping[str, Any],
    context: Mapping[str, Any],
    source_contract_hash: str,
) -> dict[str, str]:
    _scan_forbidden_keys(slate)
    _scan_forbidden_keys(context)
    assert_sealed(slate, "SLATE_HASH_DRIFT")
    assert_sealed(context, "CONTEXT_HASH_DRIFT")
    _require_sha256(source_contract_hash, "SOURCE_CONTRACT_HASH_INVALID")
    if slate.get("schema_version") != "discovery.intake.recommendation-slate-detail.0.1":
        raise ExposureFeedbackError("SLATE_SCHEMA_VERSION_UNSUPPORTED")
    if context.get("schema_version") != "discovery.intake.research-context-detail.0.1":
        raise ExposureFeedbackError("CONTEXT_SCHEMA_VERSION_UNSUPPORTED")
    if slate.get("context_hash") != context.get("content_hash"):
        raise ExposureFeedbackError("SLATE_CONTEXT_BINDING_MISMATCH")
    directions = context.get("directions")
    if not isinstance(directions, list) or not directions:
        raise ExposureFeedbackError("DIRECTION_REGISTRY_INVALID")
    direction_map: dict[str, str] = {}
    for direction in directions:
        direction_id = direction.get("direction_id")
        revision = direction.get("revision")
        if (
            not isinstance(direction_id, str)
            or not direction_id
            or not isinstance(revision, str)
            or not revision
            or direction_id in direction_map
        ):
            raise ExposureFeedbackError("DIRECTION_REGISTRY_INVALID")
        direction_map[direction_id] = revision
    selected = slate.get("selected")
    if not isinstance(selected, list):
        raise ExposureFeedbackError("SLATE_SELECTED_INVALID")
    seen_positions: set[int] = set()
    seen_candidates: set[str] = set()
    seen_works: set[str] = set()
    for item in selected:
        position = item.get("position")
        candidate_id = item.get("candidate_id")
        work_cluster_id = item.get("work_cluster_id")
        direction_id = item.get("primary_direction_id")
        if (
            type(position) is not int
            or position < 1
            or position in seen_positions
            or not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in seen_candidates
            or not isinstance(work_cluster_id, str)
            or not work_cluster_id
            or work_cluster_id in seen_works
            or direction_id not in direction_map
            or not isinstance(item.get("channel"), str)
            or not isinstance(item.get("evidence_refs"), list)
            or not item["evidence_refs"]
            or any(
                not isinstance(reference, str) or not reference
                for reference in item["evidence_refs"]
            )
            or len(set(item["evidence_refs"])) != len(item["evidence_refs"])
        ):
            raise ExposureFeedbackError("SLATE_SELECTED_INVALID")
        seen_positions.add(position)
        seen_candidates.add(candidate_id)
        seen_works.add(work_cluster_id)
    return direction_map


def exposure_events_from_slate(
    slate: Mapping[str, Any],
    context: Mapping[str, Any],
    shown_at: str,
    source_contract_hash: str,
) -> list[dict[str, Any]]:
    direction_map = validate_slate_context(slate, context, source_contract_hash)
    _parse_time(shown_at)
    events: list[dict[str, Any]] = []
    for selected in sorted(slate["selected"], key=lambda item: item["position"]):
        direction_id = selected["primary_direction_id"]
        identity = {
            "slate_id": slate["slate_id"],
            "slate_hash": slate["content_hash"],
            "position": selected["position"],
            "work_cluster_id": selected["work_cluster_id"],
            "direction_id": direction_id,
            "direction_revision": direction_map[direction_id],
            "shown_at": shown_at,
        }
        exposure_id = stable_id("EXPOSURE:", identity)
        event_id = stable_id("EVENT:", {"event_type": "EXPOSURE_RECORDED", **identity})
        events.append(
            seal_object(
                {
                    "schema_version": "discovery.verification.exposure-event.0.1",
                    "object_type": "ExposureLedgerEvent",
                    "event_type": "EXPOSURE_RECORDED",
                    "event_id": event_id,
                    "exposure_id": exposure_id,
                    "event_time": shown_at,
                    "slate_id": slate["slate_id"],
                    "slate_revision_or_hash": slate["content_hash"],
                    "position": selected["position"],
                    "candidate_id": selected["candidate_id"],
                    "work_cluster_id": selected["work_cluster_id"],
                    "channel": selected["channel"],
                    "direction_id": direction_id,
                    "direction_revision": direction_map[direction_id],
                    "source_contract_hash": source_contract_hash,
                    "evidence_refs": sorted(set(selected["evidence_refs"])),
                }
            )
        )
    return events


def make_feedback_event(
    exposure_event: Mapping[str, Any],
    decision_type: str,
    decision_revision: int,
    event_time: str,
    *,
    cooldown_until: str | None = None,
    global_confirmation: bool = False,
    manifestation_id: str | None = None,
    candidate_card_ref: str | None = None,
) -> dict[str, Any] | None:
    assert_sealed(exposure_event, "EXPOSURE_EVENT_HASH_DRIFT")
    if exposure_event.get("event_type") != "EXPOSURE_RECORDED":
        raise ExposureFeedbackError("EXPOSURE_EVENT_REQUIRED")
    if decision_type in IGNORED_INTERACTIONS:
        return None
    if decision_type not in DECISION_TYPES:
        raise ExposureFeedbackError("DECISION_TYPE_UNSUPPORTED", decision_type)
    _require_positive_integer(decision_revision, "DECISION_REVISION_INVALID")
    event_at = _parse_time(event_time)
    if event_at < _parse_time(exposure_event["event_time"]):
        raise ExposureFeedbackError("DECISION_BEFORE_EXPOSURE")
    if cooldown_until is not None and _parse_time(cooldown_until) <= event_at:
        raise ExposureFeedbackError("COOLDOWN_NOT_FUTURE")
    if decision_type == "SNOOZE" and cooldown_until is None:
        raise ExposureFeedbackError("SNOOZE_UNTIL_REQUIRED")
    if decision_type == "NO_LEGAL_FULLTEXT" and not manifestation_id:
        raise ExposureFeedbackError("MANIFESTATION_ID_REQUIRED")
    if decision_type == "MANIFESTATION_NOT_USEFUL" and not manifestation_id:
        raise ExposureFeedbackError("MANIFESTATION_ID_REQUIRED")
    if decision_type == "CARD_ERROR" and not candidate_card_ref:
        raise ExposureFeedbackError("CANDIDATE_CARD_REF_REQUIRED")
    identity = {
        "exposure_id": exposure_event["exposure_id"],
        "decision_type": decision_type,
        "decision_revision": decision_revision,
        "event_time": event_time,
    }
    payload = {
        "schema_version": "discovery.verification.exposure-event.0.1",
        "object_type": "ExposureLedgerEvent",
        "event_type": "DECISION_RECORDED",
        "event_id": stable_id("EVENT:", identity),
        "exposure_id": exposure_event["exposure_id"],
        "event_time": event_time,
        "work_cluster_id": exposure_event["work_cluster_id"],
        "direction_id": exposure_event["direction_id"],
        "direction_revision": exposure_event["direction_revision"],
        "decision_type": decision_type,
        "decision_revision": decision_revision,
        "reason_code": decision_type,
        "cooldown_until": cooldown_until,
        "global_confirmation": global_confirmation,
        "manifestation_id": manifestation_id,
        "candidate_card_ref": candidate_card_ref,
        "source_exposure_event_id": exposure_event["event_id"],
    }
    return seal_object(payload)


def make_control_event(
    exposure_event: Mapping[str, Any],
    control_type: str,
    event_revision: int,
    event_time: str,
    *,
    target_event_id: str | None = None,
) -> dict[str, Any]:
    assert_sealed(exposure_event, "EXPOSURE_EVENT_HASH_DRIFT")
    if control_type not in CONTROL_EVENT_TYPES:
        raise ExposureFeedbackError("CONTROL_EVENT_TYPE_UNSUPPORTED", control_type)
    _require_positive_integer(event_revision, "CONTROL_REVISION_INVALID")
    if control_type in {"DECISION_REVOKED", "MANUAL_RESTORE"} and not target_event_id:
        raise ExposureFeedbackError("TARGET_EVENT_ID_REQUIRED")
    if _parse_time(event_time) < _parse_time(exposure_event["event_time"]):
        raise ExposureFeedbackError("CONTROL_BEFORE_EXPOSURE")
    identity = {
        "exposure_id": exposure_event["exposure_id"],
        "control_type": control_type,
        "event_revision": event_revision,
        "event_time": event_time,
        "target_event_id": target_event_id,
    }
    return seal_object(
        {
            "schema_version": "discovery.verification.exposure-event.0.1",
            "object_type": "ExposureLedgerEvent",
            "event_type": control_type,
            "event_id": stable_id("EVENT:", identity),
            "exposure_id": exposure_event["exposure_id"],
            "event_time": event_time,
            "work_cluster_id": exposure_event["work_cluster_id"],
            "direction_id": exposure_event["direction_id"],
            "direction_revision": exposure_event["direction_revision"],
            "event_revision": event_revision,
            "target_event_id": target_event_id,
            "source_exposure_event_id": exposure_event["event_id"],
        }
    )


def _event_sort_key(event: Mapping[str, Any]) -> tuple[Any, ...]:
    event_priority = {
        "EXPOSURE_RECORDED": 0,
        "DECISION_RECORDED": 1,
        "DECISION_REVOKED": 2,
        "FEEDBACK_RESET": 3,
        "MANUAL_RESTORE": 4,
    }
    return (
        _parse_time(event["event_time"]),
        event["exposure_id"],
        event_priority[event["event_type"]],
        event.get("decision_revision", event.get("event_revision", 0)),
        event["event_id"],
    )


def build_exposure_ledger(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    duplicate_replays = 0
    for event_input in events:
        event = copy.deepcopy(dict(event_input))
        _scan_forbidden_keys(event)
        assert_sealed(event, "LEDGER_EVENT_HASH_DRIFT")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.startswith("EVENT:"):
            raise ExposureFeedbackError("EVENT_ID_INVALID")
        if event.get("event_type") not in {"EXPOSURE_RECORDED", "DECISION_RECORDED", *CONTROL_EVENT_TYPES}:
            raise ExposureFeedbackError("LEDGER_EVENT_TYPE_INVALID")
        _parse_time(event.get("event_time"))
        if event.get("schema_version") != "discovery.verification.exposure-event.0.1" or event.get("object_type") != "ExposureLedgerEvent":
            raise ExposureFeedbackError("LEDGER_EVENT_TYPE_INVALID")
        if event["event_type"] == "EXPOSURE_RECORDED":
            _require_positive_integer(event.get("position"), "SLATE_SELECTED_INVALID")
            _require_sha256(
                event.get("slate_revision_or_hash"), "SLATE_HASH_DRIFT"
            )
            _require_sha256(
                event.get("source_contract_hash"), "SOURCE_CONTRACT_HASH_INVALID"
            )
            evidence_refs = event.get("evidence_refs")
            if (
                not isinstance(evidence_refs, list)
                or not evidence_refs
                or any(
                    not isinstance(reference, str) or not reference
                    for reference in evidence_refs
                )
                or len(set(evidence_refs)) != len(evidence_refs)
            ):
                raise ExposureFeedbackError("SLATE_SELECTED_INVALID")
        elif event["event_type"] == "DECISION_RECORDED":
            _require_positive_integer(
                event.get("decision_revision"), "DECISION_REVISION_INVALID"
            )
            if (
                event.get("decision_type") not in DECISION_TYPES
                or event.get("reason_code") != event.get("decision_type")
                or not isinstance(event.get("global_confirmation"), bool)
            ):
                raise ExposureFeedbackError("DECISION_TYPE_UNSUPPORTED")
        else:
            _require_positive_integer(
                event.get("event_revision"), "CONTROL_REVISION_INVALID"
            )
        if event_id in unique:
            if canonical_json(unique[event_id]) != canonical_json(event):
                raise ExposureFeedbackError("EVENT_ID_COLLISION", event_id)
            duplicate_replays += 1
            continue
        unique[event_id] = event
    ordered = sorted(unique.values(), key=_event_sort_key)
    exposures: dict[str, dict[str, Any]] = {}
    decisions: dict[tuple[str, int], str] = {}
    for event in ordered:
        exposure_id = event.get("exposure_id")
        if not isinstance(exposure_id, str) or not exposure_id.startswith("EXPOSURE:"):
            raise ExposureFeedbackError("EXPOSURE_ID_INVALID")
        if event["event_type"] == "EXPOSURE_RECORDED":
            if exposure_id in exposures and exposures[exposure_id]["event_id"] != event["event_id"]:
                raise ExposureFeedbackError("EXPOSURE_ID_COLLISION", exposure_id)
            exposures[exposure_id] = event
        else:
            if exposure_id not in exposures:
                raise ExposureFeedbackError("DECISION_WITHOUT_EXPOSURE", exposure_id)
            base = exposures[exposure_id]
            for key in ("work_cluster_id", "direction_id", "direction_revision"):
                if event.get(key) != base.get(key):
                    raise ExposureFeedbackError("EVENT_EXPOSURE_BINDING_MISMATCH", key)
            if event.get("source_exposure_event_id") != base.get("event_id"):
                raise ExposureFeedbackError(
                    "EVENT_EXPOSURE_BINDING_MISMATCH",
                    "source_exposure_event_id",
                )
            if event["event_type"] == "DECISION_RECORDED":
                key = (exposure_id, event["decision_revision"])
                if key in decisions:
                    raise ExposureFeedbackError("DECISION_REVISION_COLLISION", str(key))
                decisions[key] = event["event_id"]
            if event["event_type"] in {"DECISION_REVOKED", "MANUAL_RESTORE"}:
                target = event.get("target_event_id")
                if target not in unique or unique[target].get("event_type") != "DECISION_RECORDED":
                    raise ExposureFeedbackError("CONTROL_TARGET_INVALID", str(target))
                if unique[target]["exposure_id"] != exposure_id:
                    raise ExposureFeedbackError("CONTROL_TARGET_EXPOSURE_MISMATCH")
                if _parse_time(event["event_time"]) < _parse_time(
                    unique[target]["event_time"]
                ):
                    raise ExposureFeedbackError("CONTROL_BEFORE_TARGET")
    fingerprint = sha256_json(ordered)
    return seal_object(
        {
            "schema_version": "discovery.verification.exposure-ledger.0.1",
            "object_type": "ExposureLedger",
            "writer": "RESEARCH_RECOMMENDATIONS.EXPOSURE_LEDGER_WRITER",
            "append_only": True,
            "canonical_order": "event_time+exposure_id+event_type_priority+revision+event_id",
            "events": ordered,
            "event_count": len(ordered),
            "exposure_count": len(exposures),
            "duplicate_replays_ignored": duplicate_replays,
            "replay_fingerprint": fingerprint,
            "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
        }
    )


def current_projection(ledger: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    assert_sealed(ledger, "LEDGER_HASH_DRIFT")
    cutoff = _parse_time(as_of)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in ledger["events"]:
        if _parse_time(event["event_time"]) <= cutoff:
            grouped[event["exposure_id"]].append(event)
    projections: list[dict[str, Any]] = []
    for exposure_id in sorted(grouped):
        events = sorted(grouped[exposure_id], key=_event_sort_key)
        base = next((item for item in events if item["event_type"] == "EXPOSURE_RECORDED"), None)
        if base is None:
            raise ExposureFeedbackError("PROJECTION_EXPOSURE_MISSING")
        active: dict[str, dict[str, Any]] = {}
        revoked: list[str] = []
        reset_event_ids: list[str] = []
        restore_event_ids: list[str] = []
        for event in events:
            if event["event_type"] == "DECISION_RECORDED":
                active[event["event_id"]] = event
            elif event["event_type"] == "DECISION_REVOKED":
                active.pop(event["target_event_id"], None)
                revoked.append(event["target_event_id"])
            elif event["event_type"] == "FEEDBACK_RESET":
                active.clear()
                reset_event_ids.append(event["event_id"])
            elif event["event_type"] == "MANUAL_RESTORE":
                target = active.get(event["target_event_id"])
                if target and target.get("decision_type") == "NOT_FOR_THIS_DIRECTION":
                    active.pop(event["target_event_id"], None)
                restore_event_ids.append(event["event_id"])
        active_decisions = sorted(active.values(), key=_event_sort_key)
        projections.append(
            {
                "exposure_id": exposure_id,
                "work_cluster_id": base["work_cluster_id"],
                "direction_id": base["direction_id"],
                "direction_revision": base["direction_revision"],
                "channel": base["channel"],
                "active_decisions": active_decisions,
                "active_decision_count": len(active_decisions),
                "revoked_target_event_ids": sorted(set(revoked)),
                "reset_event_ids": reset_event_ids,
                "manual_restore_event_ids": restore_event_ids,
                "history_event_count": len(events),
            }
        )
    return seal_object(
        {
            "schema_version": "discovery.verification.exposure-current-projection.0.1",
            "object_type": "ExposureCurrentProjection",
            "as_of": as_of,
            "ledger_hash": ledger["content_hash"],
            "entries": projections,
            "history_event_count": sum(item["history_event_count"] for item in projections),
        }
    )


def route_feedback(decision_event: Mapping[str, Any]) -> dict[str, Any]:
    assert_sealed(decision_event, "DECISION_EVENT_HASH_DRIFT")
    if decision_event.get("event_type") != "DECISION_RECORDED":
        raise ExposureFeedbackError("DECISION_EVENT_REQUIRED")
    decision_type = decision_event["decision_type"]
    taxonomy = FEEDBACK_TAXONOMY[decision_type]
    status = taxonomy["route"]
    if decision_type == "GLOBALLY_USELESS" and decision_event.get("global_confirmation") is not True:
        status = "GLOBAL_CONFIRMATION_REQUIRED"
    return {
        "decision_event_id": decision_event["event_id"],
        "reason_code": decision_type,
        "routing_status": status,
        "negative_interest_signal": False,
        "scope": taxonomy["scope"],
    }


def derive_scoped_suppressions(ledger: Mapping[str, Any], as_of: str) -> list[dict[str, Any]]:
    projection = current_projection(ledger, as_of)
    records: list[dict[str, Any]] = []
    for entry in projection["entries"]:
        for decision in entry["active_decisions"]:
            if decision["decision_type"] != "NOT_FOR_THIS_DIRECTION":
                continue
            identity = {
                "work_cluster_id": decision["work_cluster_id"],
                "direction_id": decision["direction_id"],
                "decision_revision": decision["decision_revision"],
            }
            records.append(
                seal_object(
                    {
                        "schema_version": "discovery.verification.scoped-suppression-record.0.1",
                        "object_type": "ScopedSuppressionRecord",
                        "record_id": stable_id("SUPPRESSION:", identity),
                        **identity,
                        "direction_revision": decision["direction_revision"],
                        "decision_event_id": decision["event_id"],
                        "exposure_id": decision["exposure_id"],
                        "reason_code": "NOT_FOR_THIS_DIRECTION",
                        "status": "ACTIVE",
                        "cooldown_until": decision.get("cooldown_until"),
                        "fuzzy_propagation": False,
                        "field_id_propagation": False,
                    }
                )
            )
    return sorted(records, key=lambda item: (item["work_cluster_id"], item["direction_id"], item["decision_revision"]))


def evaluate_candidate(
    candidate: Mapping[str, Any],
    ledger: Mapping[str, Any],
    suppressions: Sequence[Mapping[str, Any]],
    as_of: str,
    *,
    direction_transition: str = "NONE",
) -> dict[str, Any]:
    _scan_forbidden_keys(candidate)
    cutoff = _parse_time(as_of)
    if direction_transition not in DIRECTION_TRANSITIONS:
        raise ExposureFeedbackError("DIRECTION_TRANSITION_INVALID")
    for field in ("candidate_id", "work_cluster_id", "direction_id", "direction_revision", "manifestation_id"):
        if not isinstance(candidate.get(field), str) or not candidate[field]:
            raise ExposureFeedbackError("CANDIDATE_FIELD_INVALID", field)
    projection = current_projection(ledger, as_of)
    active_decisions = [
        decision
        for entry in projection["entries"]
        for decision in entry["active_decisions"]
        if decision["work_cluster_id"] == candidate["work_cluster_id"]
    ]
    result = "ELIGIBLE"
    reason = "NO_ACTIVE_ROUTE"
    disclosures: list[str] = []
    for decision in active_decisions:
        route = route_feedback(decision)["routing_status"]
        disclosures.append(decision["event_id"])
        if route == "GLOBAL_EXCLUSION":
            result, reason = "INELIGIBLE", "GLOBAL_SUPPRESSION_CONFIRMED"
            break
        if route == "GLOBAL_DEDUPE":
            result, reason = "INELIGIBLE", "GLOBAL_DEDUPE"
            break
        if route == "TIME_BOUND_SNOOZE" and decision.get("cooldown_until"):
            if cutoff < _parse_time(decision["cooldown_until"]):
                result, reason = "INELIGIBLE", "SNOOZE_ACTIVE"
                break
        if (
            route == "ACCESS_CHANGE_WAIT"
            and decision.get("manifestation_id") == candidate["manifestation_id"]
        ):
            result, reason = "INELIGIBLE", "WAITING_LEGAL_ACCESS_CHANGE"
            break
        if route == "CARD_REPAIR":
            result, reason = "INELIGIBLE", "CARD_REPAIR_REQUIRED"
            break
        if (
            route == "MANIFESTATION_SCOPE_SUPPRESSION"
            and decision.get("manifestation_id") == candidate["manifestation_id"]
        ):
            result, reason = "INELIGIBLE", "MANIFESTATION_SUPPRESSED"
            break
    if result == "ELIGIBLE":
        same_work = [record for record in suppressions if record["work_cluster_id"] == candidate["work_cluster_id"]]
        for record in same_work:
            assert_sealed(record, "SUPPRESSION_RECORD_HASH_DRIFT")
            if record["direction_id"] == candidate["direction_id"]:
                if direction_transition != "NONE" or record["direction_revision"] != candidate["direction_revision"]:
                    result, reason = "INELIGIBLE", "PENDING_DIRECTION_REMAP"
                else:
                    result, reason = "INELIGIBLE", "SUPPRESSED_EXACT_WORK_DIRECTION"
                break
        if result == "ELIGIBLE" and direction_transition != "NONE" and same_work:
            result, reason = "INELIGIBLE", "PENDING_DIRECTION_REMAP"
        if result == "ELIGIBLE" and same_work:
            active_cooldowns = [
                record["cooldown_until"]
                for record in same_work
                if record.get("cooldown_until") and cutoff < _parse_time(record["cooldown_until"])
            ]
            if active_cooldowns:
                result, reason = "INELIGIBLE", "CROSS_DIRECTION_COOLDOWN_ACTIVE"
            else:
                reason = "OTHER_DIRECTION_NOT_SUPPRESSED"
    return seal_object(
        {
            "schema_version": "discovery.verification.eligibility-projection.0.1",
            "object_type": "VerificationEligibilityProjection",
            "candidate_id": candidate["candidate_id"],
            "work_cluster_id": candidate["work_cluster_id"],
            "direction_id": candidate["direction_id"],
            "direction_revision": candidate["direction_revision"],
            "as_of": as_of,
            "eligibility": result,
            "reason_code": reason,
            "previous_decision_event_ids": sorted(set(disclosures)),
            "fuzzy_or_text_mapping_used": False,
            "upstream_eligibility_mutated": False,
        }
    )


def evaluate_resurface(
    previous_decision: Mapping[str, Any],
    candidate: Mapping[str, Any],
    trigger_fact: Mapping[str, Any],
    as_of: str,
) -> dict[str, Any]:
    assert_sealed(previous_decision, "PREVIOUS_DECISION_HASH_DRIFT")
    _scan_forbidden_keys(trigger_fact)
    _parse_time(as_of)
    for field in ("candidate_id", "work_cluster_id", "manifestation_id"):
        if not isinstance(candidate.get(field), str) or not candidate[field]:
            raise ExposureFeedbackError("RESURFACE_POLICY_BREACH", field)
    if candidate["work_cluster_id"] != previous_decision.get("work_cluster_id"):
        raise ExposureFeedbackError(
            "RESURFACE_POLICY_BREACH", "previous decision work mismatch"
        )
    trigger = trigger_fact.get("trigger")
    evidence_ref = trigger_fact.get("authority_evidence_ref")
    allowed = trigger in ALLOWED_RESURFACE_TRIGGERS and isinstance(evidence_ref, str) and bool(evidence_ref)
    reason = "TRIGGER_NOT_ALLOWED_OR_EVIDENCE_MISSING"
    if allowed and trigger == "FORMAL_MAJOR_VERSION":
        new_manifestation_id = trigger_fact.get("new_manifestation_id")
        allowed = (
            trigger_fact.get("major_change") is True
            and isinstance(new_manifestation_id, str)
            and bool(new_manifestation_id)
            and new_manifestation_id == candidate["manifestation_id"]
            and new_manifestation_id != previous_decision.get("manifestation_id")
        )
        reason = "FORMAL_MAJOR_VERSION" if allowed else "MAJOR_VERSION_EVIDENCE_INVALID"
    elif allowed and trigger == "LEGAL_ACCESS_AVAILABLE":
        allowed = (
            previous_decision.get("decision_type") == "NO_LEGAL_FULLTEXT"
            and trigger_fact.get("access_before") in {"METADATA_ONLY", "UNAVAILABLE", "RESTRICTED"}
            and trigger_fact.get("access_after") in {"RIGHTS_VERIFIED", "MATERIALIZED"}
        )
        reason = "LEGAL_ACCESS_AVAILABLE" if allowed else "LEGAL_ACCESS_CHANGE_INVALID"
    elif allowed and trigger == "CORRECTION_OR_RETRACTION":
        allowed = trigger_fact.get("official_notice") is True
        reason = "CORRECTION_OR_RETRACTION" if allowed else "OFFICIAL_NOTICE_REQUIRED"
    elif allowed and trigger == "IMPORTANT_CITATION_CHANGE":
        allowed = trigger_fact.get("important_relation") is True
        reason = "IMPORTANT_CITATION_CHANGE" if allowed else "IMPORTANT_RELATION_REQUIRED"
    elif allowed and trigger == "NEW_STABLE_DIRECTION":
        allowed = (
            trigger_fact.get("human_confirmed") is True
            and trigger_fact.get("new_direction_id") == candidate.get("direction_id")
            and candidate.get("direction_id") != previous_decision.get("direction_id")
        )
        reason = "NEW_STABLE_DIRECTION" if allowed else "NEW_DIRECTION_BINDING_INVALID"
    elif allowed and trigger == "BRIDGE_ROLE_CHANGE":
        allowed = trigger_fact.get("bridge_role_changed") is True
        reason = "BRIDGE_ROLE_CHANGE" if allowed else "BRIDGE_ROLE_CHANGE_INVALID"
    elif allowed and trigger == "MANUAL_RESTORE":
        allowed = isinstance(trigger_fact.get("restore_event_id"), str) and trigger_fact["restore_event_id"].startswith("EVENT:")
        reason = "MANUAL_RESTORE" if allowed else "RESTORE_EVENT_REQUIRED"
    elif trigger in RESURFACE_POLICY["forbidden_triggers"]:
        allowed = False
        reason = "MINOR_OR_PRESENTATIONAL_DRIFT_FORBIDDEN"
    if allowed and previous_decision.get("cooldown_until") and trigger not in {"CORRECTION_OR_RETRACTION", "MANUAL_RESTORE"}:
        if _parse_time(as_of) < _parse_time(previous_decision["cooldown_until"]):
            allowed = False
            reason = "COOLDOWN_ACTIVE"
    return seal_object(
        {
            "schema_version": "discovery.verification.resurface-evaluation.0.1",
            "object_type": "ResurfaceEvaluation",
            "candidate_id": candidate.get("candidate_id"),
            "work_cluster_id": candidate.get("work_cluster_id"),
            "previous_decision_event_id": previous_decision["event_id"],
            "trigger": trigger,
            "authority_evidence_ref": evidence_ref,
            "status": "ELIGIBLE_RESURFACE" if allowed else "RESURFACE_BLOCKED",
            "reason_code": reason,
            "previous_decision_disclosed": True,
            "history_mutated": False,
        }
    )


def validate_research_interaction_event(event: Mapping[str, Any]) -> None:
    _scan_forbidden_keys(event)
    exact_fields = {
        "schema_version",
        "object_type",
        "query_hash",
        "event_time",
        "session_hash",
        "field_dimension",
        "cited_work_id",
        "completion_state",
        "origin",
        "exposure_id",
    }
    if set(event) != exact_fields:
        raise ExposureFeedbackError("Research_INTERACTION_FIELD_SET_INVALID", str(sorted(set(event) ^ exact_fields)))
    if event.get("schema_version") != "discovery.verification.research-interaction-evidence.0.1" or event.get("object_type") != "ResearchInteractionEvidence":
        raise ExposureFeedbackError("Research_INTERACTION_SCHEMA_INVALID")
    _require_sha256(event.get("query_hash"), "QUERY_HASH_INVALID")
    _require_sha256(event.get("session_hash"), "SESSION_HASH_INVALID")
    _parse_time(event.get("event_time"))
    if event.get("origin") not in Research_ORIGINS:
        raise ExposureFeedbackError("Research_ORIGIN_INVALID")
    if not isinstance(event.get("field_dimension"), str) or not event["field_dimension"]:
        raise ExposureFeedbackError("FIELD_DIMENSION_INVALID")
    if event.get("completion_state") not in {
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "CANCELLED",
    }:
        raise ExposureFeedbackError("Research_INTERACTION_SCHEMA_INVALID")
    if event.get("cited_work_id") is not None and (
        not isinstance(event["cited_work_id"], str) or not event["cited_work_id"]
    ):
        raise ExposureFeedbackError("Research_INTERACTION_SCHEMA_INVALID")
    if event["origin"] == "Discovery_EXPOSURE_FOLLOWUP" and not event.get("exposure_id"):
        raise ExposureFeedbackError("Discovery_FOLLOWUP_EXPOSURE_REQUIRED")
    if event.get("exposure_id") is not None and not str(event["exposure_id"]).startswith("EXPOSURE:"):
        raise ExposureFeedbackError("Research_EXPOSURE_ID_INVALID")


def _decay_factor(age_days: int) -> Decimal:
    if age_days < 0:
        raise ExposureFeedbackError("INTERACTION_FROM_FUTURE")
    for item in BEHAVIOR_POLICY["decay_windows_days"]:
        if age_days <= item["max_age_days"]:
            return Decimal(item["factor"])
    return Decimal(BEHAVIOR_POLICY["older_factor"])


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def build_behavioral_digest(
    events: Sequence[Mapping[str, Any]],
    direction_bindings: Mapping[str, Mapping[str, Any]],
    interaction_mode: str,
    as_of: str,
    *,
    source_kind: str,
    exposure_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if interaction_mode not in Research_INTERACTION_MODES:
        raise ExposureFeedbackError("Research_INTERACTION_MODE_INVALID")
    if source_kind not in {"SYNTHETIC_TEST", "REAL_ADAPTER"}:
        raise ExposureFeedbackError("Research_SOURCE_KIND_INVALID")
    if source_kind == "REAL_ADAPTER":
        raise ExposureFeedbackError("Research_REAL_ADAPTER_NOT_CONFIGURED")
    cutoff = _parse_time(as_of)
    exposure_by_id: dict[str, Mapping[str, Any]] = {}
    if exposure_ledger is not None:
        assert_sealed(exposure_ledger, "LEDGER_HASH_DRIFT")
        exposure_by_id = {
            event["exposure_id"]: event
            for event in exposure_ledger.get("events", [])
            if event.get("event_type") == "EXPOSURE_RECORDED"
        }
    session_best: dict[tuple[str, str], dict[str, Any]] = {}
    unmapped = 0
    mapped_event_count = 0
    for raw_event in events:
        event = copy.deepcopy(dict(raw_event))
        validate_research_interaction_event(event)
        if (
            event["origin"] == "Discovery_EXPOSURE_FOLLOWUP"
            and event.get("exposure_id") not in exposure_by_id
        ):
            raise ExposureFeedbackError("Discovery_FOLLOWUP_EXPOSURE_REQUIRED")
        binding = direction_bindings.get(event["query_hash"])
        if (
            not isinstance(binding, Mapping)
            or binding.get("mapping_status") != "EXACT_SYNTHETIC"
            or not isinstance(binding.get("direction_id"), str)
            or not binding["direction_id"]
        ):
            unmapped += 1
            continue
        mapped_event_count += 1
        direction_id = binding["direction_id"]
        if (
            event["origin"] == "Discovery_EXPOSURE_FOLLOWUP"
            and exposure_by_id[event["exposure_id"]].get("direction_id")
            != direction_id
        ):
            raise ExposureFeedbackError("Discovery_FOLLOWUP_EXPOSURE_REQUIRED")
        origin_weight = Decimal(BEHAVIOR_POLICY["origin_weights"][event["origin"]])
        age_days = (cutoff - _parse_time(event["event_time"])).days
        weight = origin_weight * _decay_factor(age_days)
        key = (direction_id, event["session_hash"])
        enriched = {**event, "direction_id": direction_id, "event_weight": weight}
        existing = session_best.get(key)
        if existing is None or (weight, event["query_hash"], event["event_time"]) > (
            existing["event_weight"], existing["query_hash"], existing["event_time"]
        ):
            session_best[key] = enriched
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (direction_id, _), event in session_best.items():
        grouped[direction_id].append(event)
    direction_digests: list[dict[str, Any]] = []
    for direction_id in sorted(grouped):
        selected = sorted(grouped[direction_id], key=lambda item: (item["event_time"], item["session_hash"], item["query_hash"]))
        raw_weight = sum((item["event_weight"] for item in selected), Decimal("0"))
        cap = Decimal(BEHAVIOR_POLICY["per_direction_cap"])
        bounded = min(raw_weight, cap)
        user_sessions = {item["session_hash"] for item in selected if item["origin"] == "USER_INITIATED" and item["event_weight"] > 0}
        active_dates = {item["event_time"][:10] for item in selected if item["event_weight"] > 0}
        direction_digests.append(
            {
                "direction_id": direction_id,
                "unique_session_count": len({item["session_hash"] for item in selected}),
                "user_initiated_session_count": len(user_sessions),
                "active_date_count": len(active_dates),
                "selected_event_count": len(selected),
                "raw_weight": _decimal_text(raw_weight),
                "bounded_weight": _decimal_text(bounded),
                "saturation_applied": raw_weight > cap,
                "evidence_query_hashes": sorted({item["query_hash"] for item in selected}),
            }
        )
    deduplicated = mapped_event_count - len(session_best)
    return seal_object(
        {
            "schema_version": "discovery.verification.behavioral-interest-digest.0.1",
            "object_type": "BehavioralInterestDigest",
            "interaction_mode": interaction_mode,
            "source_kind": source_kind,
            "as_of": as_of,
            "directions": direction_digests,
            "input_event_count": len(events),
            "mapped_event_count": mapped_event_count,
            "unmapped_event_count": unmapped,
            "deduplicated_or_saturated_event_count": deduplicated,
            "real_adapter_read_count": 0 if source_kind == "SYNTHETIC_TEST" else len(events),
            "real_digest_or_boost_count": 0 if source_kind == "SYNTHETIC_TEST" else len(direction_digests),
            "behavior_policy": copy.deepcopy(BEHAVIOR_POLICY),
            "global_scalar_score": None,
            "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
        }
    )


def apply_behavioral_tiebreak(
    candidates: Sequence[Mapping[str, Any]],
    digest: Mapping[str, Any],
) -> dict[str, Any]:
    assert_sealed(digest, "BEHAVIOR_DIGEST_HASH_DRIFT")
    weights = {item["direction_id"]: Decimal(item["bounded_weight"]) for item in digest["directions"]}
    output = [copy.deepcopy(dict(candidate)) for candidate in candidates]
    required = {"candidate_id", "channel", "direction_id", "pre_tie_rank_key", "eligible"}
    for candidate in output:
        if set(candidate) != required or candidate["eligible"] is not True:
            raise ExposureFeedbackError("TIEBREAK_CANDIDATE_INVALID")
    groups: dict[tuple[str, bytes], list[int]] = defaultdict(list)
    for index, candidate in enumerate(output):
        groups[(candidate["channel"], canonical_json(candidate["pre_tie_rank_key"]))].append(index)
    changed_groups: list[dict[str, Any]] = []
    for (channel, pre_tie_key), indices in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        if len(indices) < 2:
            continue
        before = [output[index]["candidate_id"] for index in indices]
        ranked = sorted(
            (output[index] for index in indices),
            key=lambda item: (-weights.get(item["direction_id"], Decimal("0")), item["candidate_id"]),
        )
        for index, candidate in zip(indices, ranked, strict=True):
            output[index] = candidate
        after = [output[index]["candidate_id"] for index in indices]
        if before != after:
            changed_groups.append(
                {
                    "channel": channel,
                    "pre_tie_rank_key_sha256": hashlib.sha256(pre_tie_key).hexdigest().upper(),
                    "before": before,
                    "after": after,
                }
            )
    before_channels = [candidate["channel"] for candidate in candidates]
    after_channels = [candidate["channel"] for candidate in output]
    before_ids = sorted(candidate["candidate_id"] for candidate in candidates)
    after_ids = sorted(candidate["candidate_id"] for candidate in output)
    return seal_object(
        {
            "schema_version": "discovery.verification.behavioral-tiebreak.0.1",
            "object_type": "BehavioralTieBreakProjection",
            "ordered_candidates": output,
            "changed_groups": changed_groups,
            "scope": BEHAVIOR_POLICY["tie_break_scope"],
            "epsilon": BEHAVIOR_POLICY["epsilon"],
            "channel_positions_unchanged": before_channels == after_channels,
            "candidate_exact_set_unchanged": before_ids == after_ids,
            "channel_quotas_before": dict(sorted(Counter(before_channels).items())),
            "channel_quotas_after": dict(sorted(Counter(after_channels).items())),
            "eligibility_mutations": 0,
            "cap_mutations": 0,
            "no_backfill_mutations": 0,
            "profile_or_policy_mutations": 0,
            "global_scalar_score": None,
        }
    )


def build_policy_change_proposal(
    digest: Mapping[str, Any],
    base_policy_revision: str,
    evidence_refs: Sequence[str],
) -> dict[str, Any] | None:
    assert_sealed(digest, "BEHAVIOR_DIGEST_HASH_DRIFT")
    qualifying = [
        item
        for item in digest["directions"]
        if item["user_initiated_session_count"] >= BEHAVIOR_POLICY["proposal_min_user_sessions"]
        and item["active_date_count"] >= BEHAVIOR_POLICY["proposal_min_active_dates"]
    ]
    if not qualifying:
        return None
    if not isinstance(base_policy_revision, str) or not base_policy_revision:
        raise ExposureFeedbackError("BASE_POLICY_REVISION_INVALID")
    if not evidence_refs or any(not isinstance(item, str) or not item for item in evidence_refs):
        raise ExposureFeedbackError("PROPOSAL_EVIDENCE_REFS_INVALID")
    proposal_seed = {
        "base_policy_revision": base_policy_revision,
        "digest_hash": digest["content_hash"],
        "directions": [item["direction_id"] for item in qualifying],
    }
    proposal_id = stable_id("PROPOSAL:", proposal_seed)
    body = {
        "schema_version": "discovery.verification.exposure-policy-change-proposal.0.1",
        "object_type": "ExposurePolicyChangeProposal",
        "proposal_id": proposal_id,
        "base_policy_revision": base_policy_revision,
        "target_revision": "UNASSIGNED_UNTIL_HUMAN_CONFIRMATION",
        "status": "PENDING_HUMAN_CONFIRMATION",
        "direction_ids": sorted(item["direction_id"] for item in qualifying),
        "digest_hash": digest["content_hash"],
        "evidence_refs": sorted(set(evidence_refs)),
        "proposed_change": "REVIEW_EXPOSURE_BUDGET_ONLY",
        "applied": False,
        "profile_writes": 0,
        "policy_writes": 0,
    }
    body["proposal_hash"] = sha256_json(body)
    return seal_object(body)


def state_immutability_proof(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    required = {"profile", "policy", "directions", "intake_slate"}
    if set(before) != required or set(after) != required:
        raise ExposureFeedbackError("IMMUTABILITY_INPUT_SET_INVALID")
    before_hashes = {key: sha256_json(before[key]) for key in sorted(required)}
    after_hashes = {key: sha256_json(after[key]) for key in sorted(required)}
    result = {
        "before_hashes": before_hashes,
        "after_hashes": after_hashes,
        "unchanged": before_hashes == after_hashes,
        "changed_keys": [key for key in sorted(required) if before_hashes[key] != after_hashes[key]],
        "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
    }
    if not result["unchanged"]:
        raise ExposureFeedbackError(
            "LONG_TERM_STATE_MUTATION_BREACH",
            ",".join(result["changed_keys"]),
        )
    return result
