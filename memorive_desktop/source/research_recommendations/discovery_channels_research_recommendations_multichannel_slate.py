"""DISCOVERY-CHANNELS sandbox candidate: deterministic five-channel finite slate composer."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any


CHANNELS = ("CORE", "ADJACENT", "BRIDGE", "HORIZON", "COVERAGE_REPAIR")
PRIMARY_CHANNEL_PRIORITY = (
    "HORIZON",
    "COVERAGE_REPAIR",
    "BRIDGE",
    "CORE",
    "ADJACENT",
)
CAP_ORDER = ("author", "institution", "journal", "source", "direction")
ACCESS_RANK = {"UNKNOWN": 0, "METADATA_ONLY": 1, "OPEN": 2}
NOVELTY_RANK = {"FALSE": 0, "UNKNOWN": 1, "TRUE": 2}
ZERO_SIDE_EFFECTS = {
    "profile_writes": 0,
    "policy_writes": 0,
    "direction_writes": 0,
    "library_writes": 0,
    "artifact_registry_writes": 0,
    "production_writes": 0,
    "behavior_events_consumed": 0,
}


class SlateCompositionError(ValueError):
    """Fail-closed Intake composition error carrying a stable code."""

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


def seal_object(value: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(value))
    body.pop("content_hash", None)
    body["content_hash"] = sha256_json(body)
    return body


def _assert_sealed(value: Mapping[str, Any], code: str) -> None:
    body = copy.deepcopy(dict(value))
    recorded = body.pop("content_hash", None)
    if not isinstance(recorded, str) or recorded != sha256_json(body):
        raise SlateCompositionError(code, "content_hash mismatch")


def _parse_aware_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise SlateCompositionError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SlateCompositionError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SlateCompositionError(code, "timezone required")
    return parsed


def _input_hashes(context: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, str]:
    return {
        "profile": sha256_json(context["profile"]),
        "policy": sha256_json(context["policy"]),
        "directions": sha256_json(context["directions"]),
        "plan": plan["content_hash"],
    }


def validate_context(context: Mapping[str, Any]) -> None:
    _assert_sealed(context, "CONTEXT_HASH_DRIFT")
    if context.get("schema_version") != "discovery.intake.research-context-detail.0.1":
        raise SlateCompositionError("CONTEXT_SCHEMA_VERSION_UNSUPPORTED")
    if context.get("is_synthetic") is not True:
        raise SlateCompositionError("REAL_PROFILE_OR_POLICY_FORBIDDEN")

    directions = context.get("directions")
    if not isinstance(directions, list) or len(directions) < 5:
        raise SlateCompositionError("DIRECTION_REGISTRY_INVALID")
    direction_ids = [item.get("direction_id") for item in directions]
    if any(not value for value in direction_ids) or len(set(direction_ids)) != len(direction_ids):
        raise SlateCompositionError("DIRECTION_REGISTRY_INVALID")
    if any(item.get("human_confirmed") is not True for item in directions):
        raise SlateCompositionError("DIRECTION_NOT_HUMAN_CONFIRMED")

    profile = context.get("profile", {})
    policy = context.get("policy", {})
    profile_refs = profile.get("direction_refs", [])
    if (
        profile.get("object_type") != "ResearchInterestProfile"
        or profile.get("explicit_user_source") is not True
        or profile.get("behavior_direct_write_forbidden") is not True
        or not isinstance(profile_refs, list)
        or not profile_refs
        or any(not isinstance(value, str) or not value for value in profile_refs)
        or len(set(profile_refs)) != len(profile_refs)
        or not set(profile_refs).issubset(direction_ids)
    ):
        raise SlateCompositionError("PROFILE_INVALID")
    forbidden_profile_fields = {
        "batch_size",
        "channel_targets",
        "concentration_caps",
        "whitespace_slots",
        "no_backfill",
        "max_direction_share",
    }
    if forbidden_profile_fields.intersection(profile):
        raise SlateCompositionError("PROFILE_POLICY_FIELDS_OVERLAP")

    targets = policy.get("channel_targets", {})
    caps = policy.get("concentration_caps", {})
    batch_size = policy.get("batch_size")
    whitespace_slots = policy.get("whitespace_slots")
    if (
        policy.get("object_type") != "ResearchExposurePolicy"
        or policy.get("human_confirmed") is not True
        or policy.get("no_backfill") is not True
        or not isinstance(targets, Mapping)
        or not isinstance(caps, Mapping)
        or set(targets) != set(CHANNELS)
        or set(caps) != set(CAP_ORDER)
        or any(type(value) is not int or value < 0 for value in targets.values())
        or any(type(value) is not int or value < 1 for value in caps.values())
        or type(batch_size) is not int
        or batch_size < 1
        or type(whitespace_slots) is not int
        or whitespace_slots < 0
        or sum(targets.values()) + whitespace_slots != batch_size
    ):
        raise SlateCompositionError("POLICY_INVALID")
    max_direction_share = policy.get("max_direction_share")
    if (
        isinstance(max_direction_share, bool)
        or not isinstance(max_direction_share, (int, float))
        or not 0 < max_direction_share <= 1
    ):
        raise SlateCompositionError("POLICY_DIRECTION_SHARE_INVALID")

    anchors = context.get("horizon_anchors", [])
    if not anchors:
        raise SlateCompositionError("HORIZON_ANCHOR_MISSING")
    anchor_ids = set()
    for anchor in anchors:
        anchor_id = anchor.get("anchor_id") if isinstance(anchor, Mapping) else None
        if (
            not isinstance(anchor, Mapping)
            or not isinstance(anchor_id, str)
            or not anchor_id.startswith("HORIZON:")
            or anchor_id in anchor_ids
            or anchor.get("object_type") != "HorizonAnchor"
            or anchor.get("direction_id") not in direction_ids
            or not isinstance(anchor.get("description"), str)
            or not anchor.get("description")
            or anchor.get("independent_of_local_similarity") is not True
            or anchor.get("independent_of_behavior") is not True
            or anchor.get("human_confirmed") is not True
        ):
            raise SlateCompositionError("HORIZON_ANCHOR_INVALID")
        anchor_ids.add(anchor_id)

    gaps = context.get("coverage_gaps", [])
    if not gaps:
        raise SlateCompositionError("COVERAGE_GAP_MISSING")
    gap_ids = set()
    for gap in gaps:
        gap_id = gap.get("gap_id") if isinstance(gap, Mapping) else None
        if (
            not isinstance(gap, Mapping)
            or not isinstance(gap_id, str)
            or not gap_id.startswith("GAP:")
            or gap_id in gap_ids
            or gap.get("direction_id") not in direction_ids
            or not isinstance(gap.get("reason"), str)
            or not gap.get("reason")
            or gap.get("frozen") is not True
        ):
            raise SlateCompositionError("COVERAGE_GAP_INVALID")
        gap_ids.add(gap_id)

    session = context.get("session", {})
    if (
        session.get("source") != "SYNTHETIC_TEST_LOCAL"
        or session.get("long_term_profile_mutation") is not False
        or session.get("long_term_policy_mutation") is not False
    ):
        raise SlateCompositionError("SESSION_LONG_TERM_MUTATION_FORBIDDEN")


def validate_plan(plan: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    _assert_sealed(plan, "PLAN_HASH_DRIFT")
    if plan.get("schema_version") != "discovery.intake.discovery-plan-detail.0.1":
        raise SlateCompositionError("PLAN_SCHEMA_VERSION_UNSUPPORTED")
    if plan.get("is_synthetic") is not True:
        raise SlateCompositionError("NON_SYNTHETIC_PLAN_FORBIDDEN")
    policy = context["policy"]
    expected = {
        "batch_size": policy["batch_size"],
        "channel_targets": policy["channel_targets"],
        "whitespace_slots": policy["whitespace_slots"],
        "concentration_caps": policy["concentration_caps"],
    }
    observed = {name: plan.get(name) for name in expected}
    if observed != expected:
        raise SlateCompositionError("PLAN_POLICY_DRIFT")
    if plan.get("context_hash") != context["content_hash"]:
        raise SlateCompositionError("PLAN_CONTEXT_HASH_MISMATCH")
    if tuple(plan.get("primary_channel_priority", [])) != PRIMARY_CHANNEL_PRIORITY:
        raise SlateCompositionError("PRIMARY_CHANNEL_PRIORITY_DRIFT")
    if plan.get("tie_break") != "CANONICAL_IDENTITY_ASC":
        raise SlateCompositionError("TIE_BREAK_DRIFT")
    if plan.get("no_backfill") != "CORE_SILENT_BACKFILL_FORBIDDEN":
        raise SlateCompositionError("NO_BACKFILL_DRIFT")
    if plan.get("history_windows_days") != [30, 90]:
        raise SlateCompositionError("HISTORY_WINDOWS_DRIFT")
    _parse_aware_datetime(plan.get("frozen_as_of"), "PLAN_FROZEN_AS_OF_INVALID")


def _eligible_channels(fact: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    metadata = fact["ranking_metadata"]
    reasons: list[str] = []
    if fact["state_axes"]["identity_status"] != "VERIFIED":
        reasons.append("IDENTITY_NOT_VERIFIED")
    if metadata["source_available"] is not True:
        reasons.append("SOURCE_UNAVAILABLE")
    if fact["novelty_axes"]["library_new"] != "TRUE":
        reasons.append("LIBRARY_NOVELTY_NOT_TRUE")
    if fact["novelty_axes"]["first_seen_new"] == "FALSE":
        reasons.append("ALREADY_SEEN")
    if reasons:
        return [], sorted(set(reasons))

    profile_directions = set(context["profile"]["direction_refs"])
    direction_ids = {item["direction_id"] for item in context["directions"]}
    anchors_by_id = {item["anchor_id"]: item for item in context["horizon_anchors"]}
    gaps_by_id = {item["gap_id"]: item for item in context["coverage_gaps"]}
    signals = fact["ranking_metadata"]["direction_signals"]
    signalled_direction_ids = {
        item["direction_id"] for item in signals if item["match"] != "NONE"
    }
    channels: set[str] = set()
    if any(item["match"] == "EXACT" and item["direction_id"] in profile_directions for item in signals):
        channels.add("CORE")
    if any(item["match"] == "RELATED" and item["direction_id"] in direction_ids for item in signals):
        channels.add("ADJACENT")
    bridge_refs = set(metadata["bridge_direction_refs"])
    if len(bridge_refs) >= 2 and bridge_refs.issubset(direction_ids) and "BRIDGE_BETWEEN_DIRECTIONS" in fact["relation_types"]:
        channels.add("BRIDGE")
    horizon_refs = set(metadata["horizon_anchor_refs"])
    if horizon_refs:
        if (
            not horizon_refs.issubset(anchors_by_id)
            or any(
                anchors_by_id[anchor_id]["direction_id"] not in signalled_direction_ids
                for anchor_id in horizon_refs
            )
        ):
            return [], ["HORIZON_ANCHOR_NOT_FROZEN"]
        channels.add("HORIZON")
    gap_refs = set(metadata["coverage_gap_refs"])
    if gap_refs:
        if (
            not gap_refs.issubset(gaps_by_id)
            or any(
                gaps_by_id[gap_id]["direction_id"] not in signalled_direction_ids
                for gap_id in gap_refs
            )
        ):
            return [], ["COVERAGE_GAP_NOT_FROZEN"]
        channels.add("COVERAGE_REPAIR")
    if not channels:
        return [], ["NO_CHANNEL_EVIDENCE"]
    ordered = [channel for channel in PRIMARY_CHANNEL_PRIORITY if channel in channels]
    reason_codes = [f"ELIGIBLE_{channel}" for channel in ordered]
    if len(ordered) > 1:
        reason_codes.append("MULTICHANNEL_PRIMARY_PRIORITY_APPLIED")
    return ordered, sorted(reason_codes)


def _ranking_evidence(
    fact: Mapping[str, Any], primary_channel: str
) -> dict[str, Any]:
    if primary_channel in {"HORIZON", "COVERAGE_REPAIR", "BRIDGE"}:
        direction_match = 4
    elif primary_channel == "CORE":
        direction_match = 3
    else:
        direction_match = 2
    metadata = fact["ranking_metadata"]
    components = {
        "direction_match": direction_match,
        "evidence_completeness": metadata["evidence_completeness"],
        "library_novelty": NOVELTY_RANK[fact["novelty_axes"]["library_new"]],
        "timeliness": metadata["timeliness"],
        "access_posture": ACCESS_RANK[metadata["access_posture"]],
    }
    return {
        "channel": primary_channel,
        "components": components,
        "comparison_key": [
            components["direction_match"],
            components["evidence_completeness"],
            components["library_novelty"],
            components["timeliness"],
            components["access_posture"],
        ],
        "tie_break_identity": fact["canonical_identity"],
        "evidence_refs": sorted(
            set(fact["ranking_metadata"]["evidence_refs"] + fact["relation_refs"])
        ),
        "global_scalar_score": None,
    }


def build_recall_candidates(
    facts: Sequence[Mapping[str, Any]], context: Mapping[str, Any], plan: Mapping[str, Any]
) -> list[dict[str, Any]]:
    validate_context(context)
    validate_plan(plan, context)
    candidate_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    for fact in facts:
        _assert_sealed(fact, "FACT_HASH_DRIFT")
        candidate_id = fact.get("candidate_id")
        if candidate_id in candidate_ids:
            raise SlateCompositionError("DUPLICATE_CANDIDATE_ID")
        candidate_ids.add(candidate_id)
        channels, reason_codes = _eligible_channels(fact, context)
        primary_channel = channels[0] if channels else None
        results.append(
            seal_object(
                {
                    "schema_version": "discovery.intake.recall-channel-candidate.0.1",
                    "object_type": "RecallChannelCandidate",
                    "candidate_id": candidate_id,
                    "work_cluster_id": fact["work_cluster_id"],
                    "fact_hash": fact["content_hash"],
                    "primary_channel": primary_channel,
                    "eligible_channels": channels,
                    "eligibility": {
                        "eligible": bool(channels),
                        "reason_codes": reason_codes,
                    },
                    "ranking_evidence": (
                        _ranking_evidence(fact, primary_channel) if primary_channel else None
                    ),
                }
            )
        )
    return sorted(results, key=lambda item: item["candidate_id"])


def _representative_key(recall: Mapping[str, Any], fact: Mapping[str, Any]) -> tuple[Any, ...]:
    metadata = fact["ranking_metadata"]
    return (
        -int(fact["state_axes"]["identity_status"] == "VERIFIED"),
        -metadata["evidence_completeness"],
        -metadata["manifestation_fitness"],
        -ACCESS_RANK[metadata["access_posture"]],
        fact["canonical_identity"],
    )


def build_candidate_clusters(
    facts: Sequence[Mapping[str, Any]], recalls: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    fact_by_id = {item["candidate_id"]: item for item in facts}
    recall_by_id = {item["candidate_id"]: item for item in recalls}
    groups: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        groups[fact["work_cluster_id"]].append(fact["candidate_id"])
    clusters: list[dict[str, Any]] = []
    for work_cluster_id, members in sorted(groups.items()):
        ordered = sorted(
            members,
            key=lambda candidate_id: _representative_key(
                recall_by_id[candidate_id], fact_by_id[candidate_id]
            ),
        )
        representative = ordered[0]
        cluster_seed = {"work_cluster_id": work_cluster_id, "members": sorted(members)}
        clusters.append(
            seal_object(
                {
                    "schema_version": "discovery.intake.candidate-cluster.0.1",
                    "object_type": "CandidateCluster",
                    "cluster_id": "Intake_CLUSTER:" + sha256_json(cluster_seed)[:24].lower(),
                    "work_cluster_id": work_cluster_id,
                    "member_candidate_ids": sorted(members),
                    "representative_candidate_id": representative,
                    "representative_reason_codes": [
                        "IDENTITY_VERIFIED",
                        "EVIDENCE_COMPLETENESS_ORDER",
                        "MANIFESTATION_FITNESS_ORDER",
                        "ACCESS_POSTURE_ORDER",
                        "CANONICAL_TIE_BREAK",
                    ],
                    "primary_channel": recall_by_id[representative]["primary_channel"],
                    "suppressed_members": [
                        {
                            "candidate_id": candidate_id,
                            "reason_codes": ["NON_REPRESENTATIVE_MANIFESTATION"],
                        }
                        for candidate_id in ordered[1:]
                    ],
                    "identity_facts_created": 0,
                }
            )
        )
    return clusters


def _rank_sort_key(recall: Mapping[str, Any]) -> tuple[Any, ...]:
    evidence = recall["ranking_evidence"]
    return tuple(-value for value in evidence["comparison_key"]) + (evidence["tie_break_identity"],)


def _cap_values(fact: Mapping[str, Any]) -> dict[str, list[str]]:
    metadata = fact["ranking_metadata"]
    return {
        "author": metadata["authors"],
        "institution": metadata["institutions"],
        "journal": [metadata["journal_id"]],
        "source": [metadata["source_id"]],
        "direction": [metadata["primary_direction_id"]],
    }


def _first_cap_violation(
    fact: Mapping[str, Any], counters: Mapping[str, Counter[str]], limits: Mapping[str, int]
) -> str | None:
    values = _cap_values(fact)
    for dimension in CAP_ORDER:
        if any(counters[dimension][value] + 1 > limits[dimension] for value in values[dimension]):
            return dimension
    return None


def compose_slate(
    facts: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    behavior_events: Sequence[Mapping[str, Any]] | None = None,
    production_write_target: str | None = None,
) -> dict[str, Any]:
    """Compose a deterministic finite slate without any cross-channel backfill."""

    if behavior_events:
        raise SlateCompositionError("BEHAVIOR_INPUT_NOT_ACCEPTED_IN_Intake")
    if production_write_target is not None:
        raise SlateCompositionError("PRODUCTION_WRITE_FORBIDDEN_Intake")
    context_before = canonical_json(context)
    plan_before = canonical_json(plan)
    facts_before = canonical_json(facts)
    validate_context(context)
    validate_plan(plan, context)
    input_hashes_before = _input_hashes(context, plan)
    recalls = build_recall_candidates(facts, context, plan)
    clusters = build_candidate_clusters(facts, recalls)

    fact_by_id = {item["candidate_id"]: item for item in facts}
    recall_by_id = {item["candidate_id"]: item for item in recalls}
    representatives = {item["representative_candidate_id"] for item in clusters}
    suppressed = {
        item["candidate_id"]: item["reason_codes"]
        for cluster in clusters
        for item in cluster["suppressed_members"]
    }
    unselected: dict[str, dict[str, Any]] = {
        candidate_id: {
            "candidate_id": candidate_id,
            "work_cluster_id": fact_by_id[candidate_id]["work_cluster_id"],
            "channel": recall_by_id[candidate_id]["primary_channel"],
            "reason_codes": reason_codes,
        }
        for candidate_id, reason_codes in suppressed.items()
    }
    for candidate_id in representatives:
        recall = recall_by_id[candidate_id]
        if not recall["eligibility"]["eligible"]:
            unselected[candidate_id] = {
                "candidate_id": candidate_id,
                "work_cluster_id": recall["work_cluster_id"],
                "channel": recall["primary_channel"],
                "reason_codes": recall["eligibility"]["reason_codes"],
            }

    counters: dict[str, Counter[str]] = {dimension: Counter() for dimension in CAP_ORDER}
    limits = plan["concentration_caps"]
    targets = plan["channel_targets"]
    selected: list[dict[str, Any]] = []
    selected_by_channel: Counter[str] = Counter()
    rejected_by_cap: list[str] = []

    for channel in CHANNELS:
        pool = [
            recall_by_id[candidate_id]
            for candidate_id in representatives
            if recall_by_id[candidate_id]["primary_channel"] == channel
            and recall_by_id[candidate_id]["eligibility"]["eligible"]
        ]
        for recall in sorted(pool, key=_rank_sort_key):
            candidate_id = recall["candidate_id"]
            fact = fact_by_id[candidate_id]
            violation = _first_cap_violation(fact, counters, limits)
            if violation is not None:
                reason = violation.upper() + "_CAP_REACHED"
                unselected[candidate_id] = {
                    "candidate_id": candidate_id,
                    "work_cluster_id": fact["work_cluster_id"],
                    "channel": channel,
                    "reason_codes": [reason],
                }
                rejected_by_cap.append(candidate_id + "|" + reason)
                continue
            if selected_by_channel[channel] >= targets[channel]:
                unselected[candidate_id] = {
                    "candidate_id": candidate_id,
                    "work_cluster_id": fact["work_cluster_id"],
                    "channel": channel,
                    "reason_codes": ["CHANNEL_QUOTA_EXHAUSTED"],
                }
                continue
            for dimension, values in _cap_values(fact).items():
                for value in values:
                    counters[dimension][value] += 1
            selected_by_channel[channel] += 1
            selected.append(
                {
                    "position": len(selected) + 1,
                    "candidate_id": candidate_id,
                    "work_cluster_id": fact["work_cluster_id"],
                    "channel": channel,
                    "primary_direction_id": fact["ranking_metadata"]["primary_direction_id"],
                    "reason_codes": [
                        f"SELECTED_{channel}",
                        "CHANNEL_RANKED_LEXICOGRAPHIC",
                        "CAPS_SATISFIED",
                    ],
                    "evidence_refs": recall["ranking_evidence"]["evidence_refs"],
                    "ranking_evidence_ref": recall["content_hash"],
                }
            )

    if len(selected) > plan["batch_size"] - plan["whitespace_slots"]:
        raise SlateCompositionError("BATCH_OR_WHITESPACE_LIMIT_BREACHED")
    selected_work_clusters = [item["work_cluster_id"] for item in selected]
    if len(selected_work_clusters) != len(set(selected_work_clusters)):
        raise SlateCompositionError("DUPLICATE_WORKCLUSTER_IN_SLATE")

    allocations = []
    for channel in CHANNELS:
        selected_count = selected_by_channel[channel]
        gap = max(0, targets[channel] - selected_count)
        allocations.append(
            {
                "channel": channel,
                "target": targets[channel],
                "selected": selected_count,
                "gap": gap,
                "unused_positions": gap,
                "status": "FILLED" if gap == 0 else "CHANNEL_UNDERFILLED",
            }
        )

    observed_counts = {
        dimension: dict(sorted(counter.items())) for dimension, counter in counters.items()
    }
    max_observed = {
        dimension: max(counter.values(), default=0) for dimension, counter in counters.items()
    }
    slate_seed = {
        "plan_hash": plan["content_hash"],
        "selected": [item["candidate_id"] for item in selected],
        "allocations": allocations,
    }
    input_hashes_after = _input_hashes(context, plan)
    slate = seal_object(
        {
            "schema_version": "discovery.intake.recommendation-slate-detail.0.1",
            "object_type": "RecommendationSlateDetail",
            "slate_id": "Intake_SLATE:" + sha256_json(slate_seed)[:24].lower(),
            "initialization_slate_ref": "RecommendationSlate",
            "plan_id": plan["plan_id"],
            "plan_hash": plan["content_hash"],
            "context_hash": context["content_hash"],
            "selected": selected,
            "unselected": sorted(unselected.values(), key=lambda item: item["candidate_id"]),
            "channel_allocations": allocations,
            "caps": {
                "limits": copy.deepcopy(limits),
                "observed_counts": observed_counts,
                "max_observed": max_observed,
                "rejected_by_cap": sorted(rejected_by_cap),
            },
            "no_backfill": "CORE_SILENT_BACKFILL_FORBIDDEN",
            "whitespace_slots": plan["whitespace_slots"],
            "ordered": True,
            "input_hashes_before": input_hashes_before,
            "input_hashes_after": input_hashes_after,
            "side_effects": copy.deepcopy(ZERO_SIDE_EFFECTS),
        }
    )
    if context_before != canonical_json(context) or plan_before != canonical_json(plan):
        raise SlateCompositionError("CONTEXT_OR_PLAN_MUTATED")
    if facts_before != canonical_json(facts):
        raise SlateCompositionError("FACT_INPUT_MUTATED")
    if input_hashes_before != input_hashes_after:
        raise SlateCompositionError("PROFILE_POLICY_DIRECTION_OR_PLAN_HASH_DRIFT")
    return {"recall_candidates": recalls, "clusters": clusters, "slate": slate}


def build_coverage_polarization_report(
    slate: Mapping[str, Any],
    history: Mapping[str, Any],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    _assert_sealed(slate, "SLATE_HASH_DRIFT")
    validate_context(context)
    validate_plan(plan, context)
    as_of = _parse_aware_datetime(
        plan.get("frozen_as_of"), "PLAN_FROZEN_AS_OF_INVALID"
    )
    complete_windows_value = history.get("complete_windows_days", [])
    if (
        not isinstance(history.get("history_id"), str)
        or not history.get("history_id")
        or not isinstance(complete_windows_value, list)
        or any(type(days) is not int for days in complete_windows_value)
        or len(set(complete_windows_value)) != len(complete_windows_value)
        or not set(complete_windows_value).issubset(plan["history_windows_days"])
    ):
        raise SlateCompositionError("HISTORY_SHAPE_INVALID")
    complete_windows = set(complete_windows_value)
    events = history.get("events", [])
    if not isinstance(events, list):
        raise SlateCompositionError("HISTORY_SHAPE_INVALID")
    direction_ids = sorted(item["direction_id"] for item in context["directions"])
    parsed_events: list[tuple[Mapping[str, Any], datetime]] = []
    event_ids: set[str] = set()
    for event in events:
        event_id = event.get("event_id") if isinstance(event, Mapping) else None
        if (
            not isinstance(event, Mapping)
            or not isinstance(event_id, str)
            or not event_id
            or event_id in event_ids
        ):
            raise SlateCompositionError("HISTORY_SHAPE_INVALID")
        event_ids.add(event_id)
        occurred_at = _parse_aware_datetime(
            event.get("occurred_at"), "HISTORY_EVENT_TIME_INVALID"
        )
        if (
            event.get("direction_id") not in direction_ids
            or event.get("channel") not in CHANNELS
        ):
            raise SlateCompositionError("HISTORY_EVENT_REFERENCE_INVALID")
        parsed_events.append((event, occurred_at))
    planned_counts = Counter(item["primary_direction_id"] for item in slate["selected"])
    max_share = context["policy"]["max_direction_share"]
    windows = []
    all_warnings: set[str] = set()
    for days in plan["history_windows_days"]:
        cutoff = as_of - timedelta(days=days)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        if days not in complete_windows:
            warnings = [f"HISTORY_COVERAGE_UNKNOWN_{days}D"]
            all_warnings.update(warnings)
            windows.append(
                {
                    "days": days,
                    "cutoff": cutoff_text,
                    "status": "UNKNOWN",
                    "denominator": None,
                    "counted_exposures": None,
                    "direction_rows": [
                        {
                            "direction_id": direction_id,
                            "planned_current_slate_count": planned_counts[direction_id],
                            "actual_exposure_count": None,
                            "share": None,
                            "concentration_cap_breach": None,
                        }
                        for direction_id in direction_ids
                    ],
                    "channel_counts": {channel: None for channel in CHANNELS},
                    "channel_gaps": {channel: None for channel in CHANNELS},
                    "warnings": warnings,
                }
            )
            continue

        in_window = []
        for event, occurred_at in parsed_events:
            if cutoff <= occurred_at <= as_of:
                in_window.append(event)
        denominator = len(in_window)
        direction_counts = Counter(item["direction_id"] for item in in_window)
        channel_counts = Counter(item["channel"] for item in in_window)
        direction_rows = []
        warnings: list[str] = []
        for direction_id in direction_ids:
            actual = direction_counts[direction_id]
            share = actual / denominator if denominator else 0.0
            breach = share > max_share
            direction_rows.append(
                {
                    "direction_id": direction_id,
                    "planned_current_slate_count": planned_counts[direction_id],
                    "actual_exposure_count": actual,
                    "share": share,
                    "concentration_cap_breach": breach,
                }
            )
            if breach:
                warnings.append(f"DIRECTION_CONCENTRATION_{days}D")
        if channel_counts["HORIZON"] == 0:
            warnings.append(f"HORIZON_ABSENT_{days}D")
        if channel_counts["COVERAGE_REPAIR"] < plan["channel_targets"]["COVERAGE_REPAIR"]:
            warnings.append(f"COVERAGE_REPAIR_UNDER_TARGET_{days}D")
        warnings = sorted(set(warnings))
        all_warnings.update(warnings)
        windows.append(
            {
                "days": days,
                "cutoff": cutoff_text,
                "status": "COMPLETE",
                "denominator": denominator,
                "counted_exposures": denominator,
                "direction_rows": direction_rows,
                "channel_counts": {channel: channel_counts[channel] for channel in CHANNELS},
                "channel_gaps": {
                    channel: max(0, plan["channel_targets"][channel] - channel_counts[channel])
                    for channel in CHANNELS
                },
                "warnings": warnings,
            }
        )

    report_seed = {"slate_hash": slate["content_hash"], "as_of": plan["frozen_as_of"], "windows": windows}
    return seal_object(
        {
            "schema_version": "discovery.intake.coverage-polarization-report.0.1",
            "object_type": "CoveragePolarizationReport",
            "report_id": "Intake_REPORT:" + sha256_json(report_seed)[:24].lower(),
            "slate_hash": slate["content_hash"],
            "as_of": plan["frozen_as_of"],
            "windows": windows,
            "warnings": sorted(all_warnings),
            "proposal_boundary": "Verification_ONLY_NO_MUTATION",
            "profile_mutations": 0,
            "policy_mutations": 0,
        }
    )


def compose_projection(
    facts: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
    history: Mapping[str, Any],
) -> dict[str, Any]:
    composition = compose_slate(facts, context, plan)
    report = build_coverage_polarization_report(
        composition["slate"], history, context, plan
    )
    return {
        "recall_candidates": composition["recall_candidates"],
        "clusters": composition["clusters"],
        "slate": composition["slate"],
        "coverage_report": report,
    }


__all__ = [
    "CAP_ORDER",
    "CHANNELS",
    "PRIMARY_CHANNEL_PRIORITY",
    "SlateCompositionError",
    "build_candidate_clusters",
    "build_coverage_polarization_report",
    "build_recall_candidates",
    "canonical_json",
    "compose_projection",
    "compose_slate",
    "seal_object",
    "sha256_json",
    "validate_context",
    "validate_plan",
]
