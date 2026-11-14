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
) -> dict[str, Any]:
    _scan_forbidden_keys(candidate)
    cutoff = _parse_time(as_of)
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
                if record["direction_revision"] != candidate["direction_revision"]:
                    result, reason = "INELIGIBLE", "PENDING_DIRECTION_REMAP"
                else:
                    result, reason = "INELIGIBLE", "SUPPRESSED_EXACT_WORK_DIRECTION"
                break
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


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
