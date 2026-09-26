from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .exam_successor import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)
from .retrieval_exam_calibration import (
    EMBEDDING_ROLE_ADEQUACY_POLICY,
    RERANKER_TWO_SLOT_POLICY,
)


Quality_NEW_CATALOG_SCHEMA = "WorkflowModelExamScoreCatalog-QualityNew-v2"
Quality_NEW_PROJECTION_SCHEMA = "WorkflowModelExamQualityNewProjection-v3"
Quality_NEW_VOLUME_POLICY_REVISION = "Quality_NEW_ADAPTIVE_EXAM_VOLUME_V3"
Quality_NEW_FLOW_POLICY_REVISION = (
    "Quality_NEW_WORKFLOW_RESIDUAL_RISK_AND_RETRIEVAL_CALIBRATION_V3"
)
Quality_NEW_EMBEDDING_REGISTRY_SCHEMA = (
    "QualityNewEmbeddingEvidenceRegistry-v2"
)
Quality_NEW_EMBEDDING_REGISTRY_LEGACY_SCHEMA = (
    "QualityNewEmbeddingEvidenceRegistry-v1"
)
Quality_NEW_EMBEDDING_DIAGNOSTIC_SCHEMA = (
    "QualityNewEmbeddingDiagnosticProjection-v2"
)

_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_TIERS = frozenset({"CONNECTIVITY", "STANDARD", "CALIBRATION"})
_SAFETY_SEVERITIES = frozenset({"MATERIAL", "CRITICAL", "INFORMATION_LOSS"})
_SEVERITIES = _SAFETY_SEVERITIES | frozenset({"PRESENTATION", "COMPLETENESS"})
_REPAIR_ROUNDS = frozenset({"NONE", "R1", "R2", "CONTROLLED_OMISSION"})
_GUARD_KINDS = frozenset({"NONE", "THEORETICAL", "INDEPENDENT", "SAME_MODEL"})

MODEL_EXAM_ROLES = (
    "ANALYSIS_PRIMARY",
    "ANALYSIS_REVIEWER",
    "CARD_DISTILLER",
    "CARD_REVIEWER",
    "DOCUMENT_PROCESSING",
    "EMBEDDING_TEXT",
    "OCR_PAGE",
    "RERANKER_TEXT",
)


DEFAULT_WORKFLOW_MAPPING: dict[str, Any] = {
    "schema_version": "QualityNewWorkflowMapping-v1",
    "revision": "Desktop_CURRENT_FLOW_MODEL_MAPPING_Quality_NEW_BASELINE_V1",
    "nodes": [
        {
            "stage_index": 1,
            "node_id": "ingest",
            "model_roles": ["DOCUMENT_PROCESSING", "OCR_PAGE"],
        },
        {
            "stage_index": 2,
            "node_id": "chunk_embedding",
            "model_roles": ["EMBEDDING_TEXT"],
        },
        {
            "stage_index": 3,
            "node_id": "card_distill",
            "model_roles": ["CARD_DISTILLER"],
        },
        {
            "stage_index": 4,
            "node_id": "transport_review",
            "model_roles": ["CARD_REVIEWER"],
            "optional": True,
        },
        {
            "stage_index": 5,
            "node_id": "card_admission",
            "execution_kind": "DETERMINISTIC_STATE_GATE",
        },
        {
            "stage_index": 6,
            "node_id": "context_pack",
            "model_roles": ["RERANKER_TEXT"],
            "embedding_source_node_id": "chunk_embedding",
        },
        {
            "stage_index": 7,
            "node_id": "analysis",
            "model_roles": ["ANALYSIS_PRIMARY"],
        },
        {
            "stage_index": 8,
            "node_id": "judgment_review",
            "model_roles": ["ANALYSIS_REVIEWER"],
            "optional": True,
        },
        {
            "stage_index": 9,
            "node_id": "human_final",
            "execution_kind": "HUMAN_TERMINAL",
        },
    ],
    "guard_credit_contract": {
        "theoretical_detection_credit": False,
        "actual_detection_required": True,
        "blocking_required": True,
        "repair_reverification_required": True,
        "information_loss_requires_source_recovery": True,
        "same_model_guard_requires_mapping_canary": True,
    },
    "score_contract": {
        "node_capability_score_is_category_local": True,
        "workflow_layer_emits_universal_numeric_score": False,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    },
}


_VOLUME_POLICIES: dict[str, dict[str, Any]] = {
    "CARD_REVIEWER": {
        "connectivity": 2,
        "standard_target": 10,
        "standard_maximum": 12,
        "calibration_target": 30,
        "logical_case_count": 30,
        "qualification_ready": True,
    },
    "CARD_DISTILLER": {
        "connectivity": 2,
        "standard_target": 11,
        "standard_maximum": 16,
        "calibration_target": 25,
        "logical_case_count": 48,
        "qualification_ready": True,
    },
    "ANALYSIS_REVIEWER": {
        "connectivity": 2,
        "standard_target": 12,
        "standard_maximum": 18,
        "calibration_target": 24,
        "logical_case_count": 24,
        "qualification_ready": True,
    },
    "OCR_PAGE": {
        "connectivity": 2,
        "standard_target": 11,
        "standard_maximum": 13,
        "calibration_target": 24,
        "logical_case_count": 11,
        "qualification_ready": True,
    },
    "ANALYSIS_PRIMARY": {
        "connectivity": 2,
        "standard_target": 24,
        "standard_maximum": 75,
        "calibration_target": 48,
        "logical_case_count": 36,
        "qualification_ready": True,
        "panels_required_for_stable_score": 3,
        "early_stop_allowed_only_for": [
            "NON_SCORABLE_EXECUTION",
            "MODEL_FAIL_ALREADY_ESTABLISHED",
        ],
        "calibration_only_residual_triggers_repair": False,
    },
    "EMBEDDING_TEXT": {
        "connectivity": 2,
        "standard_target": 24,
        "standard_maximum": 26,
        "calibration_target": 48,
        "logical_case_count": 240,
        "corpora_per_physical_request": 10,
        "small_sample_substitution_forbidden": True,
        "qualification_ready": False,
        "qualification_blocker": (
            "LIVE_PRODUCTION_SHAPED_SUCCESSOR_REQUIRED_FOR_FORMAL_QUALIFICATION"
        ),
        "role_adequacy_threshold_defined": True,
        "role_adequacy_policy_revision": EMBEDDING_ROLE_ADEQUACY_POLICY[
            "revision"
        ],
        "role_adequacy_requires_live_or_frozen_vector_replay": True,
        "descriptive_score_available": True,
    },
    "RERANKER_TEXT": {
        "connectivity": 2,
        "standard_target": 24,
        "standard_maximum": 26,
        "calibration_target": 48,
        "logical_case_count": 24,
        "production_primary_main_calls": 18,
        "multilingual_adversarial_main_calls": 4,
        "repeat_calls": 2,
        "production_primary_language_modes": {"en_en": 16, "zh_en": 2},
        "multilingual_adversarial_language_modes": {"en_zh": 2, "zh_zh": 2},
        "primary_score_track": "production_primary",
        "multilingual_track_affects_primary_score": False,
        "qualification_ready": False,
        "qualification_blocker": (
            "TWO_DISTINCT_SLOTS_AND_FORMAL_QUALIFICATION_GATE_REQUIRED"
        ),
        "role_adequacy_threshold_defined": True,
        "role_adequacy_policy_revision": RERANKER_TWO_SLOT_POLICY["revision"],
        "role_adequacy_required_distinct_slots": 2,
        "descriptive_score_available": True,
    },
    "DOCUMENT_PROCESSING": {
        "connectivity": 2,
        "standard_target": 0,
        "standard_maximum": 0,
        "calibration_target": 0,
        "logical_case_count": 0,
        "qualification_ready": False,
        "qualification_blocker": "EXECUTOR_AND_REFERENCE_PACK_REQUIRED",
    },
}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value.upper()))


def _empty_embedding_registry() -> dict[str, Any]:
    return {
        "schema_version": Quality_NEW_EMBEDDING_REGISTRY_SCHEMA,
        "status": "NOT_PROVIDED",
        "profiles": [],
    }


def _validated_embedding_registry(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the aggregate-only Quality evidence used by the EXE cache.

    The registry contains no Form, Gold, candidate text, or provider payload. It
    only restores public aggregate facts that were lost when the Desktop v9 cache
    flattened the original Quality scorecards.
    """

    if value is None:
        return _empty_embedding_registry()
    if not isinstance(value, Mapping):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_INVALID")
    registry = deepcopy(dict(value))
    schema_version = registry.get("schema_version")
    if schema_version not in {
        Quality_NEW_EMBEDDING_REGISTRY_LEGACY_SCHEMA,
        Quality_NEW_EMBEDDING_REGISTRY_SCHEMA,
    }:
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_SCHEMA_INVALID")
    if registry.get("status") == "NOT_PROVIDED":
        if registry != _empty_embedding_registry():
            raise ValueError("Quality_NEW_EMBEDDING_EMPTY_REGISTRY_INVALID")
        return registry
    role_aligned = schema_version == Quality_NEW_EMBEDDING_REGISTRY_SCHEMA
    expected_status = (
        "AUTHORITATIVE_Quality_ROLE_ALIGNED_REPLAY_RECOVERED"
        if role_aligned
        else "AUTHORITATIVE_Quality_AGGREGATES_RECOVERED"
    )
    if (
        registry.get("status") != expected_status
        or registry.get("exam_category_id")
        != "Quality_EMBEDDING_TEXT_REFERENCE_REGRESSION"
        or registry.get("role_id") != "EMBEDDING_TEXT"
        or registry.get("aggregate_evidence_only") is not True
        or registry.get("provider_received_gold") is not False
    ):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_AUTHORITY_INVALID")
    source_authority = registry.get("source_authority")
    if not isinstance(source_authority, Mapping) or not source_authority:
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_SOURCE_MISSING")
    for source in source_authority.values():
        if (
            not isinstance(source, Mapping)
            or not isinstance(source.get("name"), str)
            or not source.get("name")
            or not _valid_sha256(source.get("sha256"))
        ):
            raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_SOURCE_INVALID")
    qualification = registry.get("qualification")
    qualification_valid = (
        isinstance(qualification, Mapping)
        and qualification.get("quality_threshold_defined") is role_aligned
        and qualification.get("qualification_eligible") is False
        and qualification.get("acceptance_verdict") == "NOT_ASSESSED"
        and qualification.get("model_failure_established") is False
        and qualification.get(
            "post_hoc_selection_scale_may_grant_qualification"
        )
        is False
    )
    if role_aligned:
        qualification_valid = bool(
            qualification_valid
            and qualification.get("role_adequacy_assessed") is True
            and qualification.get("formal_live_successor_required") is True
            and registry.get("role_adequacy_policy_revision")
            == EMBEDDING_ROLE_ADEQUACY_POLICY["revision"]
            and registry.get("frozen_vectors_replayed") is True
            and _valid_sha256(registry.get("replay_evidence_sha256"))
            and _valid_sha256(registry.get("replay_evidence_file_sha256"))
        )
    if not qualification_valid:
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_QUALIFICATION_INVALID")
    scope = registry.get("scope")
    if (
        not isinstance(scope, Mapping)
        or scope.get("forms") != 2
        or not isinstance(scope.get("tasks"), int)
        or scope.get("tasks", 0) <= 0
        or not isinstance(scope.get("corpus_packs"), int)
        or scope.get("corpus_packs", 0) <= 0
    ):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_SCOPE_INVALID")
    profiles = registry.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_PROFILES_INVALID")
    identities: set[tuple[str, str]] = set()
    profile_keys: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_PROFILE_INVALID")
        identity = (str(profile.get("profile_kind")), str(profile.get("model_name")))
        profile_key = str(profile.get("profile_key"))
        raw_score = profile.get("raw_score")
        critical = profile.get("critical_misses")
        repeatability = profile.get("repeatability")
        execution = profile.get("historical_execution")
        replay = profile.get("role_aligned_replay")
        if (
            identity[0] not in {"API", "LOCAL"}
            or not identity[1]
            or identity in identities
            or not profile_key
            or profile_key in profile_keys
            or not _valid_sha256(profile.get("source_scorecard_sha256"))
            or not isinstance(raw_score, Mapping)
            or isinstance(raw_score.get("mean"), bool)
            or not isinstance(raw_score.get("mean"), (int, float))
            or not 0 <= float(raw_score["mean"]) <= 100
            or not isinstance(critical, Mapping)
            or not isinstance(critical.get("count"), int)
            or critical.get("count", -1) < 0
            or not isinstance(repeatability, Mapping)
            or not isinstance(repeatability.get("checks"), int)
            or repeatability.get("checks", 0) <= 0
            or not isinstance(execution, Mapping)
            or not isinstance(execution.get("physical_model_calls"), int)
            or execution.get("physical_model_calls", 0) <= 0
        ):
            raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_PROFILE_INVALID")
        if role_aligned:
            if not isinstance(replay, Mapping):
                raise ValueError(
                    "Quality_NEW_EMBEDDING_REGISTRY_ROLE_REPLAY_INVALID"
                )
            assessment = replay.get("assessment")
            primary = replay.get("primary")
            replay_repeatability = replay.get("repeatability")
            if (
                replay.get("status") != "ASSESSED"
                or isinstance(replay.get("score_exact"), bool)
                or not isinstance(replay.get("score_exact"), (int, float))
                or not 0 <= float(replay["score_exact"]) <= 100
                or not isinstance(assessment, Mapping)
                or assessment.get("status") != "ASSESSED"
                or assessment.get("quality_verdict") not in {"PASS", "FAIL"}
                or float(assessment.get("score_exact", -1))
                != float(replay["score_exact"])
                or assessment.get("formal_qualification_eligible") is not False
                or assessment.get("model_fail_established") is not False
                or assessment.get("policy") != EMBEDDING_ROLE_ADEQUACY_POLICY
                or not _valid_sha256(assessment.get("assessment_sha256"))
                or not isinstance(primary, Mapping)
                or float(primary.get("production_recall_score", -1))
                != float(replay["score_exact"])
                or not isinstance(replay_repeatability, Mapping)
                or not isinstance(replay_repeatability.get("checks"), int)
                or replay_repeatability.get("checks", 0) <= 0
                or replay.get("post_hoc_selection_scale_used") is not False
                or replay.get("legacy_raw_score_preserved") is not True
                or replay.get("formal_qualification_eligible") is not False
            ):
                raise ValueError(
                    "Quality_NEW_EMBEDDING_REGISTRY_ROLE_REPLAY_INVALID"
                )
        identities.add(identity)
        profile_keys.add(profile_key)
    pairwise = registry.get("pairwise")
    if (
        not isinstance(pairwise, Mapping)
        or set(pairwise.get("profile_keys") or []) - profile_keys
        or pairwise.get("meaningful_separation") is not False
        or pairwise.get("cross_category_comparison_forbidden") is not True
    ):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_PAIRWISE_INVALID")
    if role_aligned and (
        pairwise.get("primary_score_kind")
        != "FIRST_STAGE_HIGH_RECALL_AT_15_ROLE_ALIGNED_REPLAY"
        or pairwise.get("selection_disposition")
        != "TIED_ON_ROLE_ADEQUACY_USE_OPERATIONAL_PROFILE"
    ):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_PAIRWISE_INVALID")
    return registry


def _embedding_profile(
    entry: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if entry.get("role_id") != "EMBEDDING_TEXT":
        return None
    for profile in registry.get("profiles", []):
        if (
            isinstance(profile, Mapping)
            and profile.get("profile_kind") == entry.get("profile_kind")
            and profile.get("model_name") == entry.get("model_name")
        ):
            return profile
    return None


def _embedding_diagnostics(
    entry: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any] | None:
    profile = _embedding_profile(entry, registry)
    if profile is None:
        return None
    role_aligned = (
        registry.get("schema_version")
        == Quality_NEW_EMBEDDING_REGISTRY_SCHEMA
    )
    raw_score = deepcopy(dict(profile["raw_score"]))
    replay = (
        deepcopy(dict(profile["role_aligned_replay"]))
        if role_aligned
        else None
    )
    authoritative_raw_score = float(raw_score["mean"])
    authoritative_score = (
        float(replay["score_exact"])
        if replay is not None
        else authoritative_raw_score
    )
    source_score = float(entry["score_exact"])
    historical_critical = int(profile["critical_misses"]["count"])
    authoritative_critical = (
        int(replay["primary"]["critical_miss_count"])
        if replay is not None
        else historical_critical
    )
    source_critical = int(entry.get("failed_exam_item_count") or 0)
    authoritative_calls = int(profile["historical_execution"]["physical_model_calls"])
    source_calls = int(entry.get("physical_model_calls") or 0)
    score_correction = round(authoritative_score - source_score, 6)
    findings: list[str] = []
    if score_correction:
        findings.append(
            "SOURCE_CATALOG_SCORE_DIFFERS_FROM_ROLE_ALIGNED_REPLAY"
            if role_aligned
            else "SOURCE_CATALOG_SCORE_DIFFERS_FROM_Quality_AUTHORITY"
        )
    if source_critical != authoritative_critical:
        findings.append(
            "SOURCE_CATALOG_CRITICAL_COUNT_DIFFERS_FROM_ROLE_ALIGNED_REPLAY"
            if role_aligned
            else "SOURCE_CATALOG_CRITICAL_COUNT_DIFFERS_FROM_Quality_AUTHORITY"
        )
    if source_calls != authoritative_calls:
        findings.append("SOURCE_CATALOG_CALL_COUNT_IS_PROJECTED_NOT_OBSERVED")
    score_views: dict[str, Any]
    if replay is not None:
        score_views = {
            "primary": {
                "kind": "FIRST_STAGE_HIGH_RECALL_AT_15_ROLE_ALIGNED_REPLAY",
                "score_exact": authoritative_score,
                "metrics": deepcopy(dict(replay["primary"])),
                "quality_verdict": replay["assessment"]["quality_verdict"],
                "operational_eligibility_verdict": replay["assessment"][
                    "operational_eligibility_verdict"
                ],
                "display_as_primary": True,
                "qualification_eligible": False,
            },
            "adversarial_diagnostic": {
                "kind": "LEGACY_FINE_ORDER_NONLINEAR_DEDUCTION_SCORE",
                "score": raw_score,
                "critical_misses": deepcopy(dict(profile["critical_misses"])),
                "display_as_primary": False,
                "combined_into_primary": False,
                "qualification_eligible": False,
            },
            "historical_post_hoc_selection": {
                "kind": "POST_HOC_MONOTONIC_SELECTION_SCALE",
                "score": deepcopy(dict(profile["post_hoc_selection_score"])),
                "display_as_primary": False,
                "used_for_operational_eligibility": False,
                "qualification_eligible": False,
            },
        }
    else:
        score_views = {
            "primary": {
                "kind": "RAW_DEDUCTION_SCORE",
                "score": raw_score,
                "display_as_primary": True,
                "qualification_eligible": False,
            },
            "post_hoc_selection": {
                "kind": "POST_HOC_MONOTONIC_SELECTION_SCALE",
                "score": deepcopy(dict(profile["post_hoc_selection_score"])),
                "display_as_primary": False,
                "qualification_eligible": False,
            },
        }
    result = {
        "schema_version": Quality_NEW_EMBEDDING_DIAGNOSTIC_SCHEMA,
        "status": (
            "ROLE_ALIGNED_REPLAY_RECOVERED_WITH_SOURCE_CORRECTION"
            if role_aligned and findings
            else "ROLE_ALIGNED_REPLAY_RECOVERED"
            if role_aligned
            else "AUTHORITATIVE_EVIDENCE_RECOVERED_WITH_SOURCE_CORRECTION"
            if findings
            else "AUTHORITATIVE_EVIDENCE_RECOVERED"
        ),
        "profile_key": profile["profile_key"],
        "normalized_model_family": profile["normalized_model_family"],
        "source_scorecard_sha256": profile["source_scorecard_sha256"],
        "registry_sha256": canonical_sha256(registry),
        "catalog_integrity": {
            "source_catalog_score_exact": source_score,
            "authoritative_raw_score_exact": authoritative_raw_score,
            "role_aligned_primary_score_exact": (
                authoritative_score if role_aligned else None
            ),
            "score_correction": score_correction,
            "source_catalog_failed_exam_item_count": source_critical,
            "authoritative_critical_miss_count": authoritative_critical,
            "historical_adversarial_critical_miss_count": historical_critical,
            "source_catalog_physical_model_calls": source_calls,
            "authoritative_historical_physical_model_calls": authoritative_calls,
            "findings": findings,
            "source_entry_preserved": True,
        },
        "score_views": score_views,
        "error_profile": {
            "critical_misses": deepcopy(dict(profile["critical_misses"])),
            "forms": deepcopy(dict(profile["forms"])),
            "single_aggregate_is_sufficient_for_selection": False,
        },
        "repeatability": (
            deepcopy(dict(replay["repeatability"]))
            if replay is not None
            else deepcopy(dict(profile["repeatability"]))
        ),
        "role_adequacy_assessment": (
            deepcopy(dict(replay["assessment"]))
            if replay is not None
            else None
        ),
        "execution_provenance": {
            **deepcopy(dict(profile["historical_execution"])),
            "quality_new_future_physical_call_target": 5,
            "quality_new_future_physical_call_targets_by_adapter": {
                "BGE_256_INPUT_BATCH": 5,
                "QWEN_32_INPUT_BATCH": 24,
            },
            "future_input_lane_counts": {
                "production_primary": 392,
                "adversarial_diagnostic": 294,
                "repeatability": 16,
            },
            "future_batch_revision": (
                "Quality_NEW_EMBEDDING_PROVIDER_LIMIT_ALIGNED_BATCH_V1"
            ),
            "future_call_target_is_historical_observation": False,
        },
        "pairwise_interpretation": deepcopy(dict(registry["pairwise"])),
        "qualification": deepcopy(dict(registry["qualification"])),
        "legacy_language_distribution_note": (
            registry.get("legacy_language_distribution_note")
            if role_aligned
            else None
        ),
        "model_identity_note": (
            "This evidence is for BAAI/bge-m3 delivery profiles; it is not a "
            "Qwen/Qwen3-VL-Embedding-8B score."
        ),
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }
    result["diagnostic_sha256"] = canonical_sha256(result)
    return result


def policy_contract_sha256() -> str:
    return canonical_sha256(
        {
            "flow_policy_revision": Quality_NEW_FLOW_POLICY_REVISION,
            "volume_policy_revision": Quality_NEW_VOLUME_POLICY_REVISION,
            "model_exam_roles": MODEL_EXAM_ROLES,
            "volume_policies": _VOLUME_POLICIES,
            "default_workflow_mapping": DEFAULT_WORKFLOW_MAPPING,
            # Frozen score projection v1 binds its original transport contract.
            # New wire capacity policy must not relabel or invalidate old scores;
            # runtime plans independently carry the adopted successor contract.
            "output_limit_contract": json.loads(
                (Path(__file__).parent / "assets" / "quality_new_output_limit_contract_v1.json").read_text(encoding="utf-8")
            ),
            "model_failure_minimum": {
                "distinct_cases": 2,
                "distinct_panels": 2,
                "distinct_families": 2,
                "same_case_frozen_generations": 3,
            },
            "embedding_evidence_integrity": {
                "source_entry_preserved": True,
                "authoritative_aggregate_successor_allowed": True,
                "historical_calls_not_relabelled_as_future_batched_calls": True,
                "post_hoc_scale_may_grant_qualification": False,
                "single_aggregate_is_sufficient_for_selection": False,
            },
            "retrieval_role_calibration": {
                "embedding": EMBEDDING_ROLE_ADEQUACY_POLICY,
                "reranker": RERANKER_TWO_SLOT_POLICY,
                "formal_qualification_granted": False,
                "model_fail_requires_independent_evidence": True,
            },
        }
    )


def plan_exam_volume(
    role_id: str,
    tier: str,
    *,
    cache_reusable: bool = False,
    profile_kind: str | None = None,
) -> dict[str, Any]:
    """Return category-local call volume with a calibrated safety ceiling."""

    normalized_role = str(role_id).upper()
    normalized_tier = str(tier).upper()
    if normalized_role not in _VOLUME_POLICIES:
        raise ValueError("Quality_NEW_EXAM_ROLE_UNSUPPORTED")
    if normalized_tier in {"LIGHT", "HALF", "FULL"}:
        from .exam_tiers import REVISION, TIERS
        baseline = plan_exam_volume(normalized_role, "STANDARD", cache_reusable=cache_reusable, profile_kind=profile_kind)
        baseline.update(tier=normalized_tier, coverage_policy_revision=REVISION,
                        requested_fraction=TIERS[normalized_tier][0], max_directed_repairs=2,
                        actual_volume_requires_runtime_manifest=True, cost_fraction_guaranteed=False,
                        repair_allowance_varies_by_tier=False, qualification_ready=False)
        return baseline
    if normalized_tier not in _TIERS:
        raise ValueError("Quality_NEW_EXAM_TIER_INVALID")
    policy = deepcopy(_VOLUME_POLICIES[normalized_role])
    generative_output = normalized_role != "EMBEDDING_TEXT"
    common: dict[str, Any] = {
        "schema_version": "QualityNewExamVolumePlan-v1",
        "policy_revision": Quality_NEW_VOLUME_POLICY_REVISION,
        "role_id": normalized_role,
        "tier": normalized_tier,
        "profile_kind": profile_kind,
        "output_limit_policy": (
            "CALIBRATED_CEILING_WITH_MODEL_PROVIDER_PHYSICAL_MINIMUM"
            if generative_output
            else "NOT_APPLICABLE_NON_GENERATIVE_EMBEDDING"
        ),
        "global_output_token_cap": (
            WORKFLOW_EXAM_REPLY_TOKEN_CEILING if generative_output else None
        ),
        "output_limit_contract": (
            workflow_exam_output_limit_contract() if generative_output else None
        ),
        "length_only_model_fail_forbidden": True,
        "length_only_semantic_repair_forbidden": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
        "cost_control_semantics": (
            "CACHE_FIRST_THEN_CATEGORY_VOLUME_AND_RECEIPT_BOUND;_"
            "INDEPENDENT_FROM_REPLY_TOKEN_CEILING_AND_MODEL_QUALITY"
        ),
        "qualification_ready": bool(policy.get("qualification_ready")),
        "descriptive_score_available": bool(
            policy.get("descriptive_score_available")
        ),
    }
    if "qualification_blocker" in policy:
        common["qualification_blocker"] = policy["qualification_blocker"]
    for field in (
        "logical_case_count",
        "corpora_per_physical_request",
        "small_sample_substitution_forbidden",
        "panels_required_for_stable_score",
        "early_stop_allowed_only_for",
        "calibration_only_residual_triggers_repair",
        "production_primary_main_calls",
        "multilingual_adversarial_main_calls",
        "repeat_calls",
        "production_primary_language_modes",
        "multilingual_adversarial_language_modes",
        "primary_score_track",
        "multilingual_track_affects_primary_score",
        "role_adequacy_threshold_defined",
        "role_adequacy_policy_revision",
        "role_adequacy_requires_live_or_frozen_vector_replay",
        "role_adequacy_required_distinct_slots",
    ):
        if field in policy:
            common[field] = deepcopy(policy[field])
    if cache_reusable:
        common.update(
            execution_mode="STABLE_CACHE_REUSE",
            physical_model_calls=0,
            target_physical_model_calls=0,
            maximum_physical_model_calls=0,
            scoreable=True,
            model_fail_may_be_established=False,
        )
        return common
    if normalized_tier == "CONNECTIVITY":
        calls = int(policy["connectivity"])
        common.update(
            execution_mode="CONNECTIVITY_ONLY",
            physical_model_calls=calls,
            target_physical_model_calls=calls,
            maximum_physical_model_calls=calls,
            scoreable=False,
            model_fail_may_be_established=False,
        )
        return common
    if normalized_tier == "STANDARD":
        target_calls = int(policy["standard_target"])
        maximum_calls = int(policy["standard_maximum"])
        if normalized_role == "ANALYSIS_PRIMARY":
            maximum_calls = 120 if str(profile_kind).upper() == "LOCAL" else 75
            common["per_panel_model_call_ceiling"] = (
                40 if str(profile_kind).upper() == "LOCAL" else 25
            )
            common["target_is_unrepaired_base_topology"] = True
            common["repair_calls_are_material_residual_driven"] = True
        common.update(
            execution_mode="CATEGORY_STANDARD_QUALIFICATION",
            physical_model_calls=target_calls,
            target_physical_model_calls=target_calls,
            maximum_physical_model_calls=maximum_calls,
            scoreable=bool(
                policy.get("qualification_ready")
                or policy.get("descriptive_score_available")
            ),
            model_fail_may_be_established=bool(policy.get("qualification_ready")),
        )
        return common
    target_calls = int(policy["calibration_target"])
    if normalized_role == "ANALYSIS_PRIMARY":
        target_calls = 120 if str(profile_kind).upper() == "LOCAL" else 75
        common["per_panel_model_call_ceiling"] = (
            40 if str(profile_kind).upper() == "LOCAL" else 25
        )
    common.update(
        execution_mode="DEVELOPER_CALIBRATION_FULL",
        physical_model_calls=target_calls,
        target_physical_model_calls=target_calls,
        maximum_physical_model_calls=target_calls,
        scoreable=True,
        model_fail_may_be_established=bool(policy.get("qualification_ready")),
    )
    return common


def _repair_burden(repair_round: str) -> dict[str, Any]:
    ordinal = {"NONE": 0, "R1": 1, "R2": 2, "CONTROLLED_OMISSION": 1}[
        repair_round
    ]
    return {
        "class": repair_round,
        "ordinal": ordinal,
        "nonlinear_ordering": "R2_GREATER_THAN_R1_GREATER_THAN_NONE",
        "cross_category_numeric_deduction_forbidden": True,
    }


def evaluate_defect_lifecycle(
    defects: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Separate intrinsic node defects from demonstrated workflow containment."""

    if isinstance(defects, (str, bytes)) or not isinstance(defects, Sequence):
        raise ValueError("Quality_NEW_DEFECTS_INVALID")
    projections: list[dict[str, Any]] = []
    for raw in defects:
        if not isinstance(raw, Mapping):
            raise ValueError("Quality_NEW_DEFECT_INVALID")
        row = dict(raw)
        severity = str(row.get("severity") or "").upper()
        repair_round = str(row.get("repair_round") or "NONE").upper()
        guard_kind = str(row.get("guard_kind") or "NONE").upper()
        if severity not in _SEVERITIES:
            raise ValueError("Quality_NEW_DEFECT_SEVERITY_INVALID")
        if repair_round not in _REPAIR_ROUNDS:
            raise ValueError("Quality_NEW_REPAIR_ROUND_INVALID")
        if guard_kind not in _GUARD_KINDS:
            raise ValueError("Quality_NEW_GUARD_KIND_INVALID")
        guard_evidence = row.get("guard_evidence_sha256")
        evidence_valid = _valid_sha256(guard_evidence)
        actual_guard = guard_kind in {"INDEPENDENT", "SAME_MODEL"} and evidence_valid
        detected = row.get("detected") is True
        blocked = row.get("blocked") is True
        repaired = row.get("repaired") is True
        reverified = row.get("reverified") is True
        escaped = row.get("escaped") is True
        source_recovered = row.get("source_recovery_verified") is True
        node_defect = row.get("introduced") is True
        completeness_debt = severity == "COMPLETENESS"
        repair_burden = _repair_burden(repair_round)
        credit = "NO_CREDIT"
        containment = "UNCONTAINED"
        stable_guard = False
        canary_required = False

        if escaped:
            containment = "ESCAPED"
        elif repair_round == "CONTROLLED_OMISSION":
            if detected and blocked and reverified and actual_guard:
                containment = "CONTROLLED_OMISSION"
                credit = "ACTUAL_CONTAINMENT_CREDIT"
                stable_guard = guard_kind == "INDEPENDENT"
                canary_required = guard_kind == "SAME_MODEL"
            else:
                containment = "UNVERIFIED_GUARD_NO_CREDIT"
        elif severity == "INFORMATION_LOSS" and not source_recovered:
            containment = "SOURCE_RECOVERY_NOT_VERIFIED"
        elif (
            detected
            and blocked
            and repaired
            and reverified
            and repair_round in {"R1", "R2"}
            and actual_guard
        ):
            credit = "ACTUAL_CONTAINMENT_CREDIT"
            if guard_kind == "SAME_MODEL":
                containment = "CONTAINED_CORRELATED_GUARD"
                canary_required = True
            else:
                containment = f"CONTAINED_AFTER_{repair_round}"
                stable_guard = True
        elif guard_kind == "THEORETICAL" or (
            guard_kind in {"INDEPENDENT", "SAME_MODEL"} and not evidence_valid
        ):
            containment = "UNVERIFIED_GUARD_NO_CREDIT"
        elif detected and blocked:
            containment = "BLOCKED_PENDING_REPAIR_OR_REVERIFICATION"

        safety_residual = (
            severity in _SAFETY_SEVERITIES
            and containment
            not in {
                "CONTAINED_AFTER_R1",
                "CONTAINED_AFTER_R2",
                "CONTAINED_CORRELATED_GUARD",
            }
        )
        projections.append(
            {
                "defect_id": row.get("defect_id"),
                "case_id": row.get("case_id"),
                "panel_id": row.get("panel_id"),
                "family_id": row.get("family_id"),
                "severity": severity,
                "node_defect_retained": node_defect,
                "downstream_visible": row.get("downstream_visible") is True,
                "actual_detection": detected,
                "blocked": blocked,
                "repair_burden": repair_burden,
                "containment_status": containment,
                "workflow_credit": credit,
                "workflow_safety_residual": safety_residual,
                "completeness_debt": completeness_debt,
                "stable_guard_generalization_eligible": stable_guard,
                "mapping_canary_required": canary_required,
                "escaped": escaped,
                "frozen_repeat_generation_count": int(
                    row.get("frozen_repeat_generation_count") or 0
                ),
            }
        )

    safety_residuals = [row for row in projections if row["workflow_safety_residual"]]
    correlated = [row for row in projections if row["mapping_canary_required"]]
    completeness = [row for row in projections if row["completeness_debt"]]
    uncontrolled_completeness = [
        row
        for row in completeness
        if row["containment_status"] != "CONTROLLED_OMISSION"
    ]
    escaped_material = [
        row
        for row in projections
        if row["escaped"] and row["severity"] in _SAFETY_SEVERITIES
    ]
    case_ids = {row["case_id"] for row in escaped_material if row["case_id"]}
    panel_ids = {row["panel_id"] for row in escaped_material if row["panel_id"]}
    family_ids = {row["family_id"] for row in escaped_material if row["family_id"]}
    repeated_same_case = any(
        row["frozen_repeat_generation_count"] >= 3 for row in escaped_material
    )
    model_fail = (
        "ESTABLISHED"
        if repeated_same_case
        or (len(case_ids) >= 2 and len(panel_ids) >= 2 and len(family_ids) >= 2)
        else "NOT_ESTABLISHED"
    )
    if safety_residuals:
        mapping_verdict = "BLOCKED"
    elif uncontrolled_completeness:
        mapping_verdict = "BLOCKED_COMPLETENESS"
    elif correlated:
        mapping_verdict = "CONDITIONALLY_READY_CANARY_REQUIRED"
    elif completeness:
        mapping_verdict = "READY_WITH_COMPLETENESS_DEBT"
    else:
        mapping_verdict = "READY"
    result = {
        "schema_version": "QualityNewDefectLifecycleAssessment-v1",
        "policy_revision": Quality_NEW_FLOW_POLICY_REVISION,
        "defects": projections,
        "summary": {
            "node_defect_count": sum(
                1 for row in projections if row["node_defect_retained"]
            ),
            "contained_count": sum(
                1
                for row in projections
                if row["workflow_credit"] == "ACTUAL_CONTAINMENT_CREDIT"
            ),
            "workflow_safety_residual_count": len(safety_residuals),
            "completeness_debt_count": len(completeness),
            "uncontrolled_completeness_count": len(uncontrolled_completeness),
            "independent_escape_case_count": len(case_ids),
            "independent_escape_panel_count": len(panel_ids),
            "independent_escape_family_count": len(family_ids),
            "current_mapping_verdict": mapping_verdict,
            "model_failure_verdict": model_fail,
            "universal_workflow_score": None,
            "cross_category_comparison_forbidden": True,
        },
    }
    result["assessment_sha256"] = canonical_sha256(result)
    return result


def _entry_repair_burden(entry: Mapping[str, Any]) -> dict[str, Any]:
    series = entry.get("series_slots")
    rounds: list[int] = []
    if isinstance(series, list):
        for row in series:
            if isinstance(row, Mapping):
                value = row.get("semantic_repair_rounds")
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    rounds.append(value)
    aggregate = entry.get("semantic_repair_rounds")
    if not rounds and isinstance(aggregate, int) and not isinstance(aggregate, bool):
        rounds = [max(0, aggregate)]
    maximum = max(rounds, default=0)
    burden_class = "R2_USED" if maximum >= 2 else "R1_ONLY" if maximum == 1 else "NONE"
    repair_burden_affects_capability_score = (
        entry.get("repair_burden_affects_capability_score") is not False
    )
    return {
        "class": burden_class,
        "per_attempt_rounds": rounds,
        "maximum_round_in_any_attempt": maximum,
        "r1_attempt_count": sum(1 for value in rounds if value == 1),
        "r2_attempt_count": sum(1 for value in rounds if value >= 2),
        "aggregate_semantic_repair_rounds_recorded": (
            aggregate if isinstance(aggregate, int) and not isinstance(aggregate, bool) else 0
        ),
        "nonlinear_ordering": "R2_GREATER_THAN_R1_GREATER_THAN_NONE",
        "source_category_score_already_contains_its_frozen_penalty": (
            repair_burden_affects_capability_score
        ),
        "repair_burden_affects_capability_score": (
            repair_burden_affects_capability_score
        ),
        "additional_cross_category_deduction_applied": False,
    }


def _node_capability_verdict(entry: Mapping[str, Any]) -> str:
    if entry.get("execution_outcome") != "COMPLETED" or entry.get("scorability") != "SCOREABLE":
        return "NOT_ASSESSED"
    if entry.get("category_score_is_descriptive") is True or entry.get("quality_verdict") == "NOT_ASSESSED":
        return "DESCRIPTIVE_ONLY"
    if entry.get("quality_verdict") == "PASS":
        return "CAPABILITY_PASS_WITHIN_REFERENCE_COHORT"
    if entry.get("quality_verdict") == "FAIL":
        return "CURRENT_CONFIGURATION_QUALITY_FAIL"
    return "NOT_ASSESSED"


def _workflow_projection(entry: Mapping[str, Any], node_verdict: str) -> dict[str, Any]:
    role_id = entry.get("role_id")
    terminal = entry.get("terminal_disposition")
    if role_id == "OCR_PAGE":
        statuses = {
            "PAGE_RECOVERY_REQUIRED_BEFORE_RELEASE": "BLOCKED_PENDING_VERIFIED_PAGE_RECOVERY",
            "READY_AFTER_DETERMINISTIC_NORMALIZATION": "READY_AFTER_DETERMINISTIC_NORMALIZATION",
            "SOURCE_COMPARISON_REQUIRED_BEFORE_RELEASE": "BLOCKED_PENDING_SOURCE_COMPARISON",
            "STRUCTURE_REVIEW_REQUIRED_BEFORE_RELEASE": "BLOCKED_PENDING_STRUCTURE_REVIEW",
            "READY": "READY",
        }
        status = statuses.get(str(terminal), "OCR_WORKFLOW_DISPOSITION_NOT_ASSESSED")
        operational_disposition_ready = status in {
            "READY",
            "READY_AFTER_DETERMINISTIC_NORMALIZATION",
        }
        release_eligible = False
    elif node_verdict == "DESCRIPTIVE_ONLY":
        status = "QUALIFICATION_THRESHOLD_NOT_DEFINED"
        release_eligible = False
        operational_disposition_ready = False
    elif node_verdict == "CURRENT_CONFIGURATION_QUALITY_FAIL":
        status = "CURRENT_CONFIGURATION_NOT_RECOMMENDED"
        release_eligible = False
        operational_disposition_ready = False
    elif node_verdict == "CAPABILITY_PASS_WITHIN_REFERENCE_COHORT":
        if int(entry.get("safety_blocking_residual_count") or 0) > 0:
            status = "CONDITIONAL_GOVERNED_DISPOSITION_REQUIRED"
        else:
            status = "NODE_CAPABILITY_PASS_GUARD_NOT_ASSESSED"
        release_eligible = False
        operational_disposition_ready = False
    elif node_verdict == "CAPABILITY_PASS_WITHIN_ROLE_ALIGNED_REPLAY":
        status = "NODE_ROLE_ADEQUACY_PASS_LIVE_QUALIFICATION_NOT_ASSESSED"
        release_eligible = False
        operational_disposition_ready = True
    else:
        status = "NOT_ASSESSED"
        release_eligible = False
        operational_disposition_ready = False
    return {
        "status": status,
        "current_mapping_release_eligible": release_eligible,
        "operational_disposition_ready": operational_disposition_ready,
        "formal_qualification_not_granted_by_reference_regression": True,
        "guard_credit_applied": False,
        "guard_evidence_status": "NOT_PRESENT_IN_SOURCE_SCORE_ENTRY",
        "universal_workflow_score": None,
        "node_position_discount_applied": False,
        "model_failure_verdict": "NOT_ESTABLISHED_FROM_CACHED_AGGREGATE",
    }


def project_cached_entry(
    entry: Mapping[str, Any],
    workflow_mapping: Mapping[str, Any] | None = None,
    embedding_evidence_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise ValueError("Quality_NEW_SOURCE_ENTRY_INVALID")
    mapping = deepcopy(dict(workflow_mapping or DEFAULT_WORKFLOW_MAPPING))
    mapping_sha256 = canonical_sha256(mapping)
    source_score_exact = entry.get("score_exact")
    if isinstance(source_score_exact, bool) or not isinstance(source_score_exact, (int, float)):
        raise ValueError("Quality_NEW_SOURCE_SCORE_INVALID")
    registry = _validated_embedding_registry(embedding_evidence_registry)
    embedding_diagnostic = _embedding_diagnostics(entry, registry)
    role_aligned_embedding = bool(
        embedding_diagnostic
        and embedding_diagnostic.get("role_adequacy_assessment") is not None
    )
    score_exact = (
        float(
            embedding_diagnostic["catalog_integrity"][
                (
                    "role_aligned_primary_score_exact"
                    if role_aligned_embedding
                    else "authoritative_raw_score_exact"
                )
            ]
        )
        if embedding_diagnostic is not None
        else float(source_score_exact)
    )
    effective_quality_verdict = (
        str(
            embedding_diagnostic["role_adequacy_assessment"][
                "quality_verdict"
            ]
        )
        if role_aligned_embedding
        else str(entry.get("quality_verdict"))
    )
    node_verdict = (
        "CAPABILITY_PASS_WITHIN_ROLE_ALIGNED_REPLAY"
        if role_aligned_embedding and effective_quality_verdict == "PASS"
        else "CURRENT_CONFIGURATION_QUALITY_FAIL"
        if role_aligned_embedding and effective_quality_verdict == "FAIL"
        else _node_capability_verdict(entry)
    )
    identity_basis = {
        "node_id": entry.get("node_id"),
        "role_id": entry.get("role_id"),
        "exam_category_id": entry.get("exam_category_id"),
        "profile_kind": entry.get("profile_kind"),
        "model_name": entry.get("model_name"),
        "model_digest": entry.get("model_digest"),
        "thinking": entry.get("thinking"),
        "thinking_mode": entry.get("thinking_mode"),
        "tier": entry.get("tier"),
        "reference_pack_sha256": entry.get("reference_pack_sha256"),
        "scoring_protocol_sha256": entry.get("scoring_protocol_sha256"),
        "scoring_projection_revision": entry.get("scoring_projection_revision"),
        "sample_manifest_sha256": entry.get("sample_manifest_sha256"),
        "comparison_binding_sha256": entry.get("comparison_binding_sha256"),
        "evidence_sha256": entry.get("evidence_sha256"),
        "source_catalog_score_exact": float(source_score_exact),
        "score_exact": score_exact,
        "quality_verdict": effective_quality_verdict,
        "embedding_evidence_registry_sha256": (
            canonical_sha256(registry) if embedding_diagnostic is not None else None
        ),
    }
    node_binding = canonical_sha256(identity_basis)
    projection: dict[str, Any] = {
        "schema_version": Quality_NEW_PROJECTION_SCHEMA,
        "policy_revision": Quality_NEW_FLOW_POLICY_REVISION,
        "policy_contract_sha256": policy_contract_sha256(),
        "node_capability": {
            "score_exact": score_exact,
            "score": int(round(score_exact)),
            "source_catalog_score_exact": float(source_score_exact),
            "score_semantics": (
                "FIRST_STAGE_HIGH_RECALL_AT_15_ROLE_ALIGNED_FROZEN_VECTOR_REPLAY"
                if role_aligned_embedding
                else "AUTHORITATIVE_Quality_R0_5_RAW_DEDUCTION_SCORE_WITH_DIAGNOSTIC_PROFILE"
                if embedding_diagnostic is not None
                else entry.get("score_semantics")
            ),
            "quality_verdict": effective_quality_verdict,
            "verdict": node_verdict,
            "source_qualification_verdict": entry.get("qualification_verdict"),
            "formal_qualification_eligible": False,
            "model_failure_verdict": "NOT_ESTABLISHED_FROM_CACHED_AGGREGATE",
            "category_score_is_descriptive": (
                False
                if role_aligned_embedding
                else entry.get("category_score_is_descriptive") is True
            ),
            "unverified_recovery_is_score": False,
            "horizontal_comparison_eligible": entry.get("horizontal_comparison_eligible") is True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        },
        "repair_burden": _entry_repair_burden(entry),
        "workflow_projection": _workflow_projection(entry, node_verdict),
        "cache_projection": {
            "intrinsic_score_reusable": entry.get("cache_reuse_status") == "STABLE_REUSE",
            "flow_projection_reusable_only_with_same_mapping": True,
            "scorer_change_requires_model_rerun": False,
            "mapping_change_invalidates_intrinsic_score": False,
            "mapping_change_invalidates_flow_projection": True,
            "new_external_model_calls": 0,
            "source_catalog_correction_applied": bool(
                embedding_diagnostic
                and embedding_diagnostic["catalog_integrity"]["findings"]
            ),
            "role_aligned_replay_applied": role_aligned_embedding,
        },
        "workflow_mapping_revision": mapping.get("revision"),
        "workflow_mapping_sha256": mapping_sha256,
        "node_capability_binding_sha256": node_binding,
        "workflow_projection_binding_sha256": canonical_sha256(
            {
                "node_capability_binding_sha256": node_binding,
                "workflow_mapping_sha256": mapping_sha256,
                "policy_revision": Quality_NEW_FLOW_POLICY_REVISION,
            }
        ),
    }
    if embedding_diagnostic is not None:
        projection["embedding_diagnostics"] = embedding_diagnostic
    projection["projection_sha256"] = canonical_sha256(projection)
    return projection


def _coverage(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    current = sorted({str(row.get("role_id")) for row in entries if row.get("role_id")})
    missing = sorted(set(MODEL_EXAM_ROLES) - set(current))
    return {
        "model_role_count": len(MODEL_EXAM_ROLES),
        "current_scored_roles": current,
        "missing_or_unqualified_roles": missing,
        "deterministic_or_human_nodes_not_model_exam_roles": [
            "CARD_ADMISSION",
            "HUMAN_FINAL",
        ],
    }


def build_quality_new_catalog(
    source_catalog: Mapping[str, Any],
    workflow_mapping: Mapping[str, Any] | None = None,
    embedding_evidence_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(source_catalog, Mapping):
        raise ValueError("Quality_NEW_SOURCE_CATALOG_INVALID")
    if source_catalog.get("schema_version") != "WorkflowModelExamScoreCatalog-v9":
        raise ValueError("Quality_NEW_SOURCE_CATALOG_SCHEMA_INVALID")
    entries = source_catalog.get("entries")
    historical = source_catalog.get("historical_entries")
    if not isinstance(entries, list) or not isinstance(historical, list):
        raise ValueError("Quality_NEW_SOURCE_CATALOG_SHAPE_INVALID")
    expected_source_hash = canonical_sha256(
        {key: value for key, value in source_catalog.items() if key != "catalog_sha256"}
    )
    if source_catalog.get("catalog_sha256") != expected_source_hash:
        raise ValueError("Quality_NEW_SOURCE_CATALOG_HASH_INVALID")
    mapping = deepcopy(dict(workflow_mapping or DEFAULT_WORKFLOW_MAPPING))
    embedding_registry = _validated_embedding_registry(
        embedding_evidence_registry
    )
    wrappers: list[dict[str, Any]] = []
    for raw in entries:
        source_row = deepcopy(dict(raw))
        wrapper = {
            "source_entry": source_row,
            "source_entry_sha256": canonical_sha256(source_row),
            "quality_new": project_cached_entry(
                source_row,
                mapping,
                embedding_registry,
            ),
        }
        wrapper["entry_sha256"] = canonical_sha256(wrapper)
        wrappers.append(wrapper)
    candidate: dict[str, Any] = {
        "schema_version": Quality_NEW_CATALOG_SCHEMA,
        "source_catalog_schema_version": source_catalog["schema_version"],
        "source_catalog_sha256": source_catalog["catalog_sha256"],
        "policy_revision": Quality_NEW_FLOW_POLICY_REVISION,
        "volume_policy_revision": Quality_NEW_VOLUME_POLICY_REVISION,
        "policy_contract_sha256": policy_contract_sha256(),
        "workflow_mapping": mapping,
        "workflow_mapping_sha256": canonical_sha256(mapping),
        "embedding_evidence_registry": embedding_registry,
        "embedding_evidence_registry_sha256": canonical_sha256(
            embedding_registry
        ),
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
        "entries": wrappers,
        "historical_entries": deepcopy(historical),
        "coverage": _coverage(entries),
        "new_model_calls": {
            "external": 0,
            "local": 0,
            "semantics": "OFFLINE_SUCCESSOR_PROJECTION_OF_FROZEN_OUTPUTS",
        },
    }
    candidate["catalog_sha256"] = canonical_sha256(candidate)
    return candidate


def validate_quality_new_catalog(
    value: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    required = {
        "schema_version",
        "source_catalog_schema_version",
        "source_catalog_sha256",
        "policy_revision",
        "volume_policy_revision",
        "policy_contract_sha256",
        "workflow_mapping",
        "workflow_mapping_sha256",
        "embedding_evidence_registry",
        "embedding_evidence_registry_sha256",
        "cross_category_comparison_forbidden",
        "global_ranking_forbidden",
        "entries",
        "historical_entries",
        "coverage",
        "new_model_calls",
        "catalog_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("Quality_NEW_CATALOG_SHAPE_INVALID")
    if value.get("schema_version") != Quality_NEW_CATALOG_SCHEMA:
        raise ValueError("Quality_NEW_CATALOG_SCHEMA_INVALID")
    if value.get("source_catalog_schema_version") != "WorkflowModelExamScoreCatalog-v9":
        raise ValueError("Quality_NEW_SOURCE_CATALOG_SCHEMA_INVALID")
    if value.get("policy_contract_sha256") != policy_contract_sha256():
        raise ValueError("Quality_NEW_POLICY_CONTRACT_HASH_INVALID")
    mapping = value.get("workflow_mapping")
    if not isinstance(mapping, Mapping) or value.get("workflow_mapping_sha256") != canonical_sha256(mapping):
        raise ValueError("Quality_NEW_MAPPING_HASH_INVALID")
    embedding_registry = _validated_embedding_registry(
        value.get("embedding_evidence_registry")
    )
    if value.get("embedding_evidence_registry_sha256") != canonical_sha256(
        embedding_registry
    ):
        raise ValueError("Quality_NEW_EMBEDDING_REGISTRY_HASH_INVALID")
    raw_entries = value.get("entries")
    raw_historical = value.get("historical_entries")
    if not isinstance(raw_entries, list) or not isinstance(raw_historical, list):
        raise ValueError("Quality_NEW_CATALOG_ENTRIES_INVALID")
    accepted: list[dict[str, Any]] = []
    source_entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping) or set(raw) != {
            "source_entry",
            "source_entry_sha256",
            "quality_new",
            "entry_sha256",
        }:
            raise ValueError("Quality_NEW_ENTRY_SHAPE_INVALID")
        source_entry = raw.get("source_entry")
        projection = raw.get("quality_new")
        if not isinstance(source_entry, Mapping) or not isinstance(projection, Mapping):
            raise ValueError("Quality_NEW_ENTRY_INVALID")
        if raw.get("source_entry_sha256") != canonical_sha256(source_entry):
            raise ValueError("Quality_NEW_SOURCE_ENTRY_HASH_INVALID")
        expected_projection = project_cached_entry(
            source_entry,
            mapping,
            embedding_registry,
        )
        if dict(projection) != expected_projection:
            raise ValueError("Quality_NEW_ENTRY_HASH_INVALID")
        if raw.get("entry_sha256") != canonical_sha256(
            {key: item for key, item in raw.items() if key != "entry_sha256"}
        ):
            raise ValueError("Quality_NEW_ENTRY_HASH_INVALID")
        source_row = deepcopy(dict(source_entry))
        source_entries.append(source_row)
        accepted.append({**source_row, "quality_new": deepcopy(dict(projection))})
    source_unsigned = {
        "schema_version": value["source_catalog_schema_version"],
        "entries": source_entries,
        "historical_entries": raw_historical,
    }
    if value.get("source_catalog_sha256") != canonical_sha256(source_unsigned):
        raise ValueError("Quality_NEW_SOURCE_CATALOG_HASH_INVALID")
    if value.get("coverage") != _coverage(source_entries):
        raise ValueError("Quality_NEW_COVERAGE_INVALID")
    if value.get("cross_category_comparison_forbidden") is not True or value.get("global_ranking_forbidden") is not True:
        raise ValueError("Quality_NEW_COMPARISON_SCOPE_INVALID")
    if value.get("catalog_sha256") != canonical_sha256(
        {key: item for key, item in value.items() if key != "catalog_sha256"}
    ):
        raise ValueError("Quality_NEW_CATALOG_HASH_INVALID")
    return tuple(accepted), tuple(deepcopy(raw_historical))


__all__ = [
    "DEFAULT_WORKFLOW_MAPPING",
    "MODEL_EXAM_ROLES",
    "Quality_NEW_CATALOG_SCHEMA",
    "Quality_NEW_EMBEDDING_DIAGNOSTIC_SCHEMA",
    "Quality_NEW_EMBEDDING_REGISTRY_LEGACY_SCHEMA",
    "Quality_NEW_EMBEDDING_REGISTRY_SCHEMA",
    "Quality_NEW_FLOW_POLICY_REVISION",
    "Quality_NEW_PROJECTION_SCHEMA",
    "Quality_NEW_VOLUME_POLICY_REVISION",
    "build_quality_new_catalog",
    "canonical_sha256",
    "evaluate_defect_lifecycle",
    "plan_exam_volume",
    "policy_contract_sha256",
    "project_cached_entry",
    "validate_quality_new_catalog",
]
