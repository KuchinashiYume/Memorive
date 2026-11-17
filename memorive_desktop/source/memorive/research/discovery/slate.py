"""The candidate slate: a fixed, deterministic set of candidates composed from five channels.

The composition is shared with the sandbox candidate
(recommendations/discovery_channels_research_recommendations_multichannel_slate.py); this module holds the
desktop's own checks and channel rules (DESKTOP_RULES) and passes them in. The discovery run
(desktop.py) calls compose_projection.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from memorive.research.recommendations import (
    discovery_channels_research_recommendations_multichannel_slate as shared_slate,
)
from memorive.research.recommendations.discovery_channels_research_recommendations_multichannel_slate import (  # the code both modules share; one copy is kept there
    SlateCompositionError,
    canonical_json,
    sha256_json,
    seal_object,
    _assert_sealed,
    _parse_aware_datetime,
)


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


def validate_context(context: Mapping[str, Any]) -> None:
    _assert_sealed(context, "CONTEXT_HASH_DRIFT")
    if context.get("schema_version") != "desktop.desktop.research-context.1":
        raise SlateCompositionError("CONTEXT_SCHEMA_VERSION_UNSUPPORTED")
    if context.get("is_synthetic") is not False:
        raise SlateCompositionError("REAL_PROFILE_OR_POLICY_FORBIDDEN")

    directions = context.get("directions")
    if not isinstance(directions, list) or len(directions) < 1:
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
        or any(type(value) is not int or value < 0 for value in caps.values())
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
    if not anchors and targets["HORIZON"] > 0:
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
    if not gaps and targets["COVERAGE_REPAIR"] > 0:
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
        session.get("source") != "DESKTOP_EXPLICIT_USER_REQUEST"
        or session.get("long_term_profile_mutation") is not False
        or session.get("long_term_policy_mutation") is not False
    ):
        raise SlateCompositionError("SESSION_LONG_TERM_MUTATION_FORBIDDEN")


def validate_plan(plan: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    _assert_sealed(plan, "PLAN_HASH_DRIFT")
    if plan.get("schema_version") != "desktop.desktop.discovery-plan.1":
        raise SlateCompositionError("PLAN_SCHEMA_VERSION_UNSUPPORTED")
    if plan.get("is_synthetic") is not False:
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
    if fact["state_axes"]["identity_status"] != "VERIFIED" and not (fact.get('metadata_candidate_only') and metadata.get('source_record_verified')):
        reasons.append("IDENTITY_NOT_VERIFIED")
    if metadata["source_available"] is not True:
        reasons.append("SOURCE_UNAVAILABLE")
    if fact["novelty_axes"]["library_new"] != "TRUE" and not (
        fact["novelty_axes"]["library_new"]=="UNKNOWN" and fact.get("metadata_candidate_only") is True):
        reasons.append("LIBRARY_NOVELTY_NOT_TRUE")
    if fact["novelty_axes"]["first_seen_new"] == "FALSE":
        reasons.append("ALREADY_SEEN")
    if reasons:
        return [], sorted(set(reasons))

    # Desktop successor: query recall is not a semantic match assertion.
    # Exact direction + query proof is retained and shown as metadata-only.
    if "query_recall_evidence" in metadata:
        registered={d["direction_id"] for d in context["directions"]}
        channels={e["channel"] for e in metadata["query_recall_evidence"]
            if e.get("direction_id") in registered and e.get("query_sha256") and e.get("evidence_ref")}
        if metadata.get("feedback_eligible") is not True:
            return [],["USER_FEEDBACK_EXCLUDED"]
        if 'e6_relations' in metadata:
            supported={k for k,v in metadata['e6_relations'].items() if v['supported'] and v['publication_eligible']} & {e['direction_id'] for e in metadata['query_recall_evidence']}
            channels={e['channel'] for e in metadata['query_recall_evidence'] if e['direction_id'] in supported}
            if len(supported)<2:
                channels.discard('BRIDGE')
        ordered=[c for c in PRIMARY_CHANNEL_PRIORITY if c in channels]
        return ordered,["METADATA_RELATION_EVIDENCE_NOT_MECHANISM_PROOF" if 'e6_relations' in metadata else "EXPLICIT_QUERY_RECALL_NOT_SEMANTIC_PROOF"] if ordered else ["NO_CHANNEL_EVIDENCE"]

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
        "recent_interest": (metadata.get("recent_interest",{}).get("score",0.0) if primary_channel in {"CORE","ADJACENT"} else 0.0),
        "timeliness": metadata["timeliness"],
        "access_posture": ACCESS_RANK[metadata["access_posture"]],
    }
    return {
        "channel": primary_channel,
        "components": components,
        "comparison_key": ([metadata['timeliness'],metadata.get('e6_relation_rank',0)] if metadata.get('e6_objective')=='latest' else [metadata.get('e6_relation_rank',0)] if metadata.get('e6_objective')=='related' else []) + [
            components["direction_match"],
            components["evidence_completeness"],
            components["library_novelty"],
            components["recent_interest"],
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
    return shared_slate.build_recall_candidates(facts, context, plan, rules=DESKTOP_RULES)


def _representative_key(fact: Mapping[str, Any]) -> tuple[Any, ...]:
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
            key=lambda candidate_id: _representative_key(fact_by_id[candidate_id]),
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


def _cap_values(fact: Mapping[str, Any]) -> dict[str, list[str]]:
    metadata = fact["ranking_metadata"]
    return {
        "author": metadata["authors"],
        "institution": metadata["institutions"],
        "journal": [metadata["journal_id"]] if metadata["journal_id"] else [],
        "source": [metadata["source_id"]],
        "direction": [metadata["primary_direction_id"]],
    }


DESKTOP_RULES = shared_slate.SlateRules(
    validate_context=validate_context,
    validate_plan=validate_plan,
    eligible_channels=_eligible_channels,
    ranking_evidence=_ranking_evidence,
    build_candidate_clusters=build_candidate_clusters,
    cap_values=_cap_values,
)


def compose_slate(
    facts: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    behavior_events: Sequence[Mapping[str, Any]] | None = None,
    production_write_target: str | None = None,
) -> dict[str, Any]:
    return shared_slate.compose_slate(
        facts, context, plan, behavior_events=behavior_events,
        production_write_target=production_write_target, rules=DESKTOP_RULES,
    )


def build_coverage_polarization_report(
    slate: Mapping[str, Any],
    history: Mapping[str, Any],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    return shared_slate.build_coverage_polarization_report(slate, history, context, plan, rules=DESKTOP_RULES)


def compose_projection(
    facts: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    plan: Mapping[str, Any],
    history: Mapping[str, Any],
) -> dict[str, Any]:
    return shared_slate.compose_projection(facts, context, plan, history, rules=DESKTOP_RULES)


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
