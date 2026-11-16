"""The workflow exam's part of the model runner: checking the exam catalog, planning and running
an exam of one workflow node, and the stored scores. LiveModelValidationRunner (model_validation.py)
inherits WorkflowExamRunnerMixin; these methods use the runner's own execute_* methods to send the
exam's requests.
"""
from __future__ import annotations

import hashlib
from inspect import signature
import json
from typing import Any, Mapping

from .contracts import normalize_external_api_base_url
from .model_endpoints import SAFE_MODEL, SAFE_REASON_CODE, SAFE_SHA256, SAFE_THINKING, _api_profile
from .quality_new import (
    Quality_NEW_CATALOG_SCHEMA,
    validate_quality_new_catalog,
)


WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4 = (
    "score_exact",
    "score_semantics",
    "qualification_verdict",
    "exam_category_id",
    "role_id",
    "reference_pack_id",
    "reference_pack_revision",
    "reference_pack_sha256",
    "scoring_protocol_revision",
    "scoring_protocol_sha256",
    "scoring_projection_revision",
    "comparison_binding_sha256",
    "comparison_cohort_id",
    "horizontal_comparison_eligible",
    "horizontal_comparison_allowed_only_with_same_cohort",
    "cross_category_comparison_forbidden",
    "global_ranking_forbidden",
)
WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5 = (
    "operational_eligibility_verdict",
    "operational_policy_revision",
    "operational_safety_gate",
    "safety_blocking_residual_count",
    "calibration_residual_count",
)
WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6 = (
    "exact_case_repair_rate",
    "case_score_deficit_recovery_rate",
    "repair_metadata_collision_suspect_count",
    "collision_adjusted_repair_diagnostic_rate",
)
WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7 = (
    "cache_reuse_status",
    "cache_provenance_kind",
    "cache_binding_sha256",
    "executor_ref",
    "sample_revision",
    "repair_prompt_projection_revision",
    "pointer_normalization_revision",
    "subject_transport_projection_revision",
    "core_closure_verdict",
    "core_closure_score",
    "semantic_repair_rounds",
    "failed_exam_item_count",
    "series_aggregate_method",
    "series_slots",
)
WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8 = (
    "execution_outcome",
    "scorability",
    "quality_verdict",
    "terminal_disposition",
    "executor_binding_sha256",
    "executor_binding_kind",
    "sample_manifest_sha256",
    "cache_binding",
    "category_score_is_descriptive",
    "physical_model_calls",
    "token_usage",
    "provider_received_gold",
    "local_reference_pack_embedded",
)
WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS = (
    "quality_new",
    "language_distribution_status",
    "production_workload_score_established",
    "cache_reuse_requires_executor_ref_match",
    "cache_reuse_scope",
    "model_fail_verdict",
    "model_fail_established",
    "quality_threshold_defined",
    "primary_score_track",
    "calibration_series",
    "calibration_series_aggregate_method",
    "role_adequacy_policy_revision",
)
WORKFLOW_EXAM_SCORE_METADATA_FIELDS = (
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4
    + WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5
    + WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8
    + WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS
)
WORKFLOW_EXAM_ROLE_NODE_BINDINGS = {
    "CARD_DISTILLER": frozenset({"card_distill", "ingest"}),
    "CARD_REVIEWER": frozenset({"transport_review"}),
    "ANALYSIS_PRIMARY": frozenset({"analysis"}),
    "ANALYSIS_REVIEWER": frozenset({"judgment_review"}),
    "OCR_PAGE": frozenset({"ingest"}),
    "EMBEDDING_TEXT": frozenset({"chunk_embedding"}),
    "RERANKER_TEXT": frozenset({"context_pack"}),
}


def _workflow_exam_v8_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


def _validate_workflow_exam_catalog_v8(
    value: Mapping[str, Any],
    *,
    allow_frozen_successor_cohort: bool = False,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if set(value) != {
        "schema_version",
        "entries",
        "historical_entries",
        "catalog_sha256",
    }:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SHAPE_INVALID")
    expected_catalog_sha256 = _workflow_exam_v8_sha256(
        {key: item for key, item in value.items() if key != "catalog_sha256"}
    )
    if value.get("catalog_sha256") != expected_catalog_sha256:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_HASH_INVALID")

    raw_historical = value.get("historical_entries")
    if not isinstance(raw_historical, list) or len(raw_historical) > 256:
        raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRIES_INVALID")
    historical: list[dict[str, Any]] = []
    historical_fields = {
        "cache_reuse_status",
        "historical_reason",
        "source_catalog_schema_version",
        "source_entry",
        "historical_record_sha256",
    }
    for raw in raw_historical:
        if not isinstance(raw, Mapping) or set(raw) != historical_fields:
            raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID")
        row = dict(raw)
        reason = row.get("historical_reason")
        source_schema = row.get("source_catalog_schema_version")
        if (
            row.get("cache_reuse_status") != "HISTORICAL_ONLY"
            or not isinstance(reason, str)
            or not SAFE_REASON_CODE.fullmatch(reason)
            or not isinstance(source_schema, str)
            or not SAFE_MODEL.fullmatch(source_schema)
            or not isinstance(row.get("source_entry"), Mapping)
            or row.get("historical_record_sha256")
            != _workflow_exam_v8_sha256(
                {
                    key: item
                    for key, item in row.items()
                    if key != "historical_record_sha256"
                }
            )
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID")
        historical.append(row)

    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > 64:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRIES_INVALID")
    accepted: list[dict[str, Any]] = []
    identities: set[tuple[Any, ...]] = set()
    string_fields = (
        "node_id",
        "model_name",
        "score_semantics",
        "exam_category_id",
        "role_id",
        "reference_pack_id",
        "reference_pack_revision",
        "scoring_protocol_revision",
        "scoring_projection_revision",
        "comparison_cohort_id",
        "executor_ref",
        "sample_revision",
        "terminal_disposition",
        "executor_binding_kind",
        "series_aggregate_method",
    )
    sha_fields = (
        "evidence_sha256",
        "reference_pack_sha256",
        "scoring_protocol_sha256",
        "comparison_binding_sha256",
        "executor_binding_sha256",
        "sample_manifest_sha256",
        "cache_binding_sha256",
    )
    slot_fields = {
        "sample_slot",
        "sample_manifest_sha256",
        "score_exact",
        "verdict",
        "quality_verdict",
        "operational_eligibility_verdict",
        "operational_safety_gate",
        "terminal_disposition",
        "failed_exam_item_count",
        "safety_blocking_residual_count",
        "calibration_residual_count",
        "semantic_repair_rounds",
        "evidence_sha256",
        "comparison_binding_sha256",
        "physical_model_calls",
        "token_usage",
        "estimated_cost_cny",
    }
    compact_panel_fields = {
        "exam_evidence_sha256",
        "exam_run_id",
        "first_pass_score_exact",
        "physical_model_calls",
        "sample_slot",
        "score_exact",
        "semantic_repair_rounds",
    }
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
        row = dict(raw)
        if (
            any(
                not isinstance(row.get(field), str)
                or not SAFE_MODEL.fullmatch(str(row[field]))
                for field in string_fields
            )
            or any(
                not isinstance(row.get(field), str)
                or not SAFE_SHA256.fullmatch(str(row[field]))
                for field in sha_fields
            )
            or row.get("cache_reuse_status") != "STABLE_REUSE"
            or row.get("cache_provenance_kind")
            not in (
                {
                    "THREE_SLOT_SUCCESSOR_SERIES",
                    "COMPLETE_REFERENCE_COHORT",
                    "FROZEN_SUCCESSOR_COHORT",
                }
                if allow_frozen_successor_cohort
                else {
                    "THREE_SLOT_SUCCESSOR_SERIES",
                    "COMPLETE_REFERENCE_COHORT",
                }
            )
            or row.get("execution_outcome") != "COMPLETED"
            or row.get("scorability") != "SCOREABLE"
            or row.get("verdict") not in {"PASS", "FAIL"}
            or row.get("quality_verdict")
            not in {"PASS", "FAIL", "NOT_ASSESSED"}
            or isinstance(row.get("score"), bool)
            or not isinstance(row.get("score"), int)
            or not 0 <= int(row["score"]) <= 100
            or isinstance(row.get("score_exact"), bool)
            or not isinstance(row.get("score_exact"), (int, float))
            or not 0.0 <= float(row["score_exact"]) <= 100.0
            or int(row["score"]) != int(round(float(row["score_exact"])))
            or row.get("qualification_verdict")
            not in {"PASS", "FAIL", "DISQUALIFIED", "NOT_ASSESSED"}
            or row.get("operational_eligibility_verdict")
            not in {
                "PASS",
                "SUITABLE",
                "SUITABLE_WITH_CALIBRATION",
                "SUITABLE_WITH_GOVERNED_DISPOSITION",
                "SUITABLE_WITH_DOMAIN_GUARD",
                "SUITABLE_WITH_STABILITY_MONITORING",
                "SUITABLE_WITH_RERANKER_AND_SOURCE_GUARD",
                "SUITABLE_WITH_RERANKER_STABILITY_MONITORING_AND_SOURCE_GUARD",
                "NOT_RECOMMENDED",
                "NOT_RECOMMENDED_CURRENT_CONFIGURATION",
                "REJECT",
                "NOT_ASSESSED",
            }
            or row.get("operational_safety_gate")
            not in {"PASS", "FAIL", "NOT_ASSESSED"}
            or not isinstance(row.get("category_score_is_descriptive"), bool)
            or row.get("horizontal_comparison_allowed_only_with_same_cohort")
            is not True
            or row.get("cross_category_comparison_forbidden") is not True
            or row.get("global_ranking_forbidden") is not True
            or row.get("provider_received_gold") is not False
            or not isinstance(row.get("local_reference_pack_embedded"), bool)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_ENTRY_INVALID")
        if (
            row["quality_verdict"] == "NOT_ASSESSED"
            and row["category_score_is_descriptive"] is not True
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_DESCRIPTIVE_INVALID")
        allowed_nodes = WORKFLOW_EXAM_ROLE_NODE_BINDINGS.get(str(row["role_id"]))
        if allowed_nodes is None or row["node_id"] not in allowed_nodes:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ROLE_NODE_MISMATCH")
        for field in (
            "failed_exam_item_count",
            "safety_blocking_residual_count",
            "calibration_residual_count",
            "semantic_repair_rounds",
            "physical_model_calls",
        ):
            item = row.get(field)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_COUNT_INVALID")
        usage = row.get("token_usage")
        if not isinstance(usage, Mapping) or set(usage) != {
            "prompt_tokens",
            "completion_tokens",
        }:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_USAGE_INVALID")
        if any(
            isinstance(usage.get(field), bool)
            or not isinstance(usage.get(field), int)
            or int(usage[field]) < 0
            for field in usage
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_USAGE_INVALID")

        profile_kind = row.get("profile_kind")
        if profile_kind == "API":
            if (
                not isinstance(row.get("provider_family"), str)
                or not SAFE_MODEL.fullmatch(str(row["provider_family"]))
                or not isinstance(row.get("thinking"), bool)
                or not isinstance(row.get("tier"), str)
                or not SAFE_MODEL.fullmatch(str(row["tier"]))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "provider_family": row["provider_family"],
                "model_name": row["model_name"],
                "thinking": row["thinking"],
                "tier": row["tier"],
            }
            for field, pattern in (
                ("api_platform", SAFE_MODEL),
                ("credential_ref", SAFE_MODEL),
                ("thinking_mode", SAFE_THINKING),
            ):
                if field in row:
                    item = row.get(field)
                    if not isinstance(item, str) or not pattern.fullmatch(item):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_V9_API_ROUTE_IDENTITY_INVALID"
                        )
                    model_identity[field] = item
            if "base_url" in row:
                base_url = row.get("base_url")
                try:
                    normalized_base_url = normalize_external_api_base_url(base_url)
                except (TypeError, ValueError):
                    normalized_base_url = None
                if normalized_base_url != base_url:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_API_ROUTE_IDENTITY_INVALID"
                    )
                model_identity["base_url"] = base_url
            identity = (
                row["node_id"],
                profile_kind,
                row["provider_family"],
                row["model_name"],
                row["thinking"],
                row["tier"],
                model_identity.get("api_platform"),
                model_identity.get("base_url"),
                model_identity.get("credential_ref"),
                model_identity.get("thinking_mode"),
            )
        elif profile_kind == "CLI":
            if any(
                not isinstance(row.get(field), str)
                or not SAFE_MODEL.fullmatch(str(row[field]))
                for field in ("adapter_id", "thinking_mode")
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            identity = (
                row["node_id"],
                profile_kind,
                row["adapter_id"],
                row["model_name"],
                row["thinking_mode"],
            )
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "adapter_id": row["adapter_id"],
                "model_name": row["model_name"],
                "thinking_mode": row["thinking_mode"],
            }
        elif profile_kind == "LOCAL":
            if (
                row.get("endpoint_kind") != "ollama"
                or not isinstance(row.get("model_digest"), str)
                or not SAFE_SHA256.fullmatch(str(row["model_digest"]))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            identity = (
                row["node_id"],
                profile_kind,
                row["endpoint_kind"],
                row["model_name"],
                row["model_digest"],
            )
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "endpoint_kind": row["endpoint_kind"],
                "model_name": row["model_name"],
                "model_digest": row["model_digest"],
            }
        else:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
        if identity in identities:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE_IDENTITY")
        identities.add(identity)

        series_slots = row.get("series_slots")
        if not isinstance(series_slots, list):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
        if row["cache_provenance_kind"] == "THREE_SLOT_SUCCESSOR_SERIES":
            if len(series_slots) != 3:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
            slots = []
            slot_scores = []
            evidence_hashes = []
            sample_hashes = []
            pass_all = True
            summed = {
                "failed_exam_item_count": 0,
                "safety_blocking_residual_count": 0,
                "calibration_residual_count": 0,
                "semantic_repair_rounds": 0,
                "physical_model_calls": 0,
            }
            for slot in series_slots:
                if not isinstance(slot, Mapping) or set(slot) != slot_fields:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                number = slot.get("sample_slot")
                if isinstance(number, bool) or number not in {1, 2, 3}:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                if any(
                    not isinstance(slot.get(field), str)
                    or not SAFE_SHA256.fullmatch(str(slot[field]))
                    for field in (
                        "sample_manifest_sha256",
                        "evidence_sha256",
                        "comparison_binding_sha256",
                    )
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                score = slot.get("score_exact")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not 0.0 <= float(score) <= 100.0
                    or slot.get("verdict") not in {"PASS", "FAIL"}
                    or slot.get("quality_verdict") not in {"PASS", "FAIL"}
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                slot_usage = slot.get("token_usage")
                if not isinstance(slot_usage, Mapping) or set(slot_usage) != {
                    "prompt_tokens",
                    "completion_tokens",
                }:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                for field in summed:
                    item = slot.get(field)
                    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                    summed[field] += item
                slots.append(number)
                slot_scores.append(float(score))
                evidence_hashes.append(str(slot["evidence_sha256"]))
                sample_hashes.append(str(slot["sample_manifest_sha256"]))
                pass_all &= slot["verdict"] == "PASS"
            if (
                sorted(slots) != [1, 2, 3]
                or len(set(sample_hashes)) != 3
                or abs(
                    float(row["score_exact"])
                    - round(sum(slot_scores) / 3.0, 6)
                )
                > 0.000001
                or row["verdict"] != ("PASS" if pass_all else "FAIL")
                or any(row[field] != value for field, value in summed.items())
                or row["sample_manifest_sha256"]
                != _workflow_exam_v8_sha256(sample_hashes)
                or row["evidence_sha256"]
                != _workflow_exam_v8_sha256(evidence_hashes)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
        elif (
            row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
            and series_slots
        ):
            if (
                row["role_id"] != "ANALYSIS_PRIMARY"
                or row["series_aggregate_method"] != "FROZEN_SUCCESSOR_COHORT"
                or len(series_slots) != 3
                or row.get("cohort_panel_count") != 3
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SERIES_INVALID"
                )
            compact_slots: list[int] = []
            compact_scores: list[float] = []
            compact_evidence: list[str] = []
            compact_runs: list[str] = []
            compact_calls = 0
            compact_repairs = 0
            for slot in series_slots:
                if not isinstance(slot, Mapping) or set(slot) != compact_panel_fields:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SLOT_INVALID"
                    )
                number = slot.get("sample_slot")
                score = slot.get("score_exact")
                first_pass = slot.get("first_pass_score_exact")
                calls = slot.get("physical_model_calls")
                repairs = slot.get("semantic_repair_rounds")
                evidence = slot.get("exam_evidence_sha256")
                run_id = slot.get("exam_run_id")
                if (
                    isinstance(number, bool)
                    or number not in {1, 2, 3}
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not 0.0 <= float(score) <= 100.0
                    or isinstance(first_pass, bool)
                    or not isinstance(first_pass, (int, float))
                    or not 0.0 <= float(first_pass) <= 100.0
                    or isinstance(calls, bool)
                    or not isinstance(calls, int)
                    or calls < 0
                    or isinstance(repairs, bool)
                    or not isinstance(repairs, int)
                    or repairs < 0
                    or not isinstance(evidence, str)
                    or not SAFE_SHA256.fullmatch(evidence)
                    or not isinstance(run_id, str)
                    or not SAFE_MODEL.fullmatch(run_id)
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SLOT_INVALID"
                    )
                compact_slots.append(int(number))
                compact_scores.append(float(score))
                compact_evidence.append(evidence)
                compact_runs.append(run_id)
                compact_calls += calls
                compact_repairs += repairs
            panel_range = row.get("cohort_panel_score_range")
            if (
                sorted(compact_slots) != [1, 2, 3]
                or len(set(compact_evidence)) != 3
                or len(set(compact_runs)) != 3
                or isinstance(panel_range, bool)
                or not isinstance(panel_range, (int, float))
                or abs(
                    float(panel_range)
                    - round(max(compact_scores) - min(compact_scores), 6)
                )
                > 0.000001
                or row["physical_model_calls"] < compact_calls
                or row["semantic_repair_rounds"] != compact_repairs
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SERIES_INVALID"
                )
        else:
            expected_aggregate_method = (
                "FROZEN_SUCCESSOR_COHORT"
                if row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
                else "COMPLETE_REFERENCE_COHORT"
            )
            if series_slots or row["series_aggregate_method"] != expected_aggregate_method:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_COHORT_INVALID")

        cache_binding = row.get("cache_binding")
        if not isinstance(cache_binding, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_BINDING_INVALID")
        category_binding = cache_binding.get("category_binding")
        expected_category_binding = {
            "category_id": row["exam_category_id"],
            "role_id": row["role_id"],
            "reference_pack_id": row["reference_pack_id"],
            "reference_pack_revision": row["reference_pack_revision"],
            "reference_pack_sha256": row["reference_pack_sha256"],
            "scoring_protocol_revision": row["scoring_protocol_revision"],
            "scoring_protocol_sha256": row["scoring_protocol_sha256"],
            "scoring_projection_revision": row["scoring_projection_revision"],
            "executor_ref": row["executor_ref"],
            "sample_revision": row["sample_revision"],
            "sample_manifest_sha256": row["sample_manifest_sha256"],
        }
        expected_binding_hash = _workflow_exam_v8_sha256(
            {
                key: item
                for key, item in cache_binding.items()
                if key != "cache_binding_sha256"
            }
        )
        if (
            cache_binding.get("schema_version")
            != "WorkflowModelExamCacheBinding-v2"
            or category_binding != expected_category_binding
            or cache_binding.get("model_identity") != model_identity
            or cache_binding.get("executor_binding_sha256")
            != row["executor_binding_sha256"]
            or cache_binding.get("executor_binding_kind")
            != row["executor_binding_kind"]
            or cache_binding.get("evidence_set_sha256")
            != row["evidence_sha256"]
            or cache_binding.get("cache_provenance_kind")
            != row["cache_provenance_kind"]
            or cache_binding.get("outcome_contract_revision")
            != "Desktop_EXAM_OUTCOME_CONTRACT_V1"
            or cache_binding.get("cross_category_comparison_forbidden") is not True
            or cache_binding.get("global_ranking_forbidden") is not True
            or cache_binding.get("cache_binding_sha256") != expected_binding_hash
            or row["cache_binding_sha256"] != expected_binding_hash
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_BINDING_INVALID")
        accepted.append(row)
    return tuple(accepted), tuple(historical)


class WorkflowExamRunnerMixin:
    def _resolve_exam_target(self, target: Mapping[str, Any]) -> dict[str, Any]:
        accepted = dict(target)
        if accepted.get("kind") != "BUILTIN_PROFILE":
            return accepted
        profile_ref = accepted.get("profile_ref")
        if (
            not isinstance(profile_ref, str)
            or not profile_ref.startswith("local:")
            or self._local_profile_resolver is None
        ):
            return accepted
        try:
            projection = self._local_profile_resolver(profile_ref)
        except Exception:
            return accepted
        if not isinstance(projection, Mapping):
            return accepted
        row = dict(projection)
        if (
            row.get("profile_ref") != profile_ref
            or row.get("endpoint_kind") != "ollama"
            or row.get("structured_chat_adapter")
            != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            or row.get("execution_eligible") is not True
            or row.get("exact_identity_available") is not True
            or not isinstance(row.get("chat_endpoint"), str)
            or not isinstance(row.get("model_name"), str)
            or not SAFE_MODEL.fullmatch(str(row["model_name"]))
            or not isinstance(row.get("model_digest"), str)
            or not SAFE_SHA256.fullmatch(str(row["model_digest"]).upper())
        ):
            return accepted
        return {
            **row,
            "kind": "LOCAL",
            "config_id": profile_ref,
            "profile_ref": profile_ref,
            "model_digest": str(row["model_digest"]).upper(),
            "connection_status": "AVAILABLE",
        }

    def _resolve_ocr_exam_target(self, node, target):
        accepted = self._resolve_exam_target(target)
        if (node.get('node_id') != 'ingest' or accepted.get('kind') != 'CLI'
                or accepted.get('adapter_id') != 'codex_cli'
                or accepted.get('connection_status') != 'AVAILABLE'):
            return accepted
        from .cli_image_identity import bind_saved_cli_identity, FIELDS
        if any(key in accepted for key in FIELDS):
            return accepted
        executable = self._executable_resolver(accepted.get('executable'))
        if executable is None:
            return accepted
        proxy_mode, proxy_address, _ = self._preferences()
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        accepted, _ = bind_saved_cli_identity(accepted, executable,
            process=self._process,
            environment=self._safe_environment('codex_cli', self._scratch_root,
                                              proxy_mode, proxy_address),
            root=self._scratch_root)
        return accepted

    @staticmethod
    def _validate_workflow_exam_catalog(
        value: Mapping[str, Any] | None,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        if value is None:
            return (), ()
        schema_version = value.get("schema_version")
        if schema_version == Quality_NEW_CATALOG_SCHEMA:
            return validate_quality_new_catalog(value)
        if schema_version == "WorkflowModelExamScoreCatalog-v8":
            return _validate_workflow_exam_catalog_v8(value)
        if schema_version == "WorkflowModelExamScoreCatalog-v9":
            return _validate_workflow_exam_catalog_v8(
                value,
                allow_frozen_successor_cohort=True,
            )
        if schema_version not in {
            "WorkflowModelExamScoreCatalog-v1",
            "WorkflowModelExamScoreCatalog-v2",
            "WorkflowModelExamScoreCatalog-v3",
            "WorkflowModelExamScoreCatalog-v4",
            "WorkflowModelExamScoreCatalog-v5",
            "WorkflowModelExamScoreCatalog-v6",
            "WorkflowModelExamScoreCatalog-v7",
        }:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_SCHEMA_INVALID")
        historical_entries: tuple[dict[str, Any], ...] = ()
        if schema_version == "WorkflowModelExamScoreCatalog-v7":
            if set(value) != {
                "schema_version",
                "entries",
                "historical_entries",
                "catalog_sha256",
            }:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V7_SHAPE_INVALID")
            catalog_sha256 = value.get("catalog_sha256")
            expected_catalog_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        key: item
                        for key, item in value.items()
                        if key != "catalog_sha256"
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest().upper()
            if (
                not isinstance(catalog_sha256, str)
                or not SAFE_SHA256.fullmatch(catalog_sha256)
                or catalog_sha256 != expected_catalog_sha256
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_HASH_INVALID")
            raw_historical = value.get("historical_entries")
            if not isinstance(raw_historical, list) or len(raw_historical) > 64:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRIES_INVALID"
                )
            accepted_historical: list[dict[str, Any]] = []
            for raw_historical_row in raw_historical:
                if not isinstance(raw_historical_row, Mapping) or set(
                    raw_historical_row
                ) != {
                    "cache_reuse_status",
                    "historical_reason",
                    "source_catalog_schema_version",
                    "source_entry",
                    "historical_record_sha256",
                }:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID"
                    )
                historical_row = dict(raw_historical_row)
                source_entry = historical_row.get("source_entry")
                historical_hash = historical_row.get(
                    "historical_record_sha256"
                )
                expected_historical_hash = hashlib.sha256(
                    json.dumps(
                        {
                            key: item
                            for key, item in historical_row.items()
                            if key != "historical_record_sha256"
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest().upper()
                if (
                    historical_row.get("cache_reuse_status")
                    != "HISTORICAL_ONLY"
                    or historical_row.get("historical_reason")
                    != "INCOMPATIBLE_CARD_REVIEWER_PRE_SUCCESSOR_SINGLE_RUN"
                    or historical_row.get("source_catalog_schema_version")
                    != "WorkflowModelExamScoreCatalog-v6"
                    or not isinstance(source_entry, Mapping)
                    or source_entry.get("role_id") != "CARD_REVIEWER"
                    or not isinstance(source_entry.get("score"), int)
                    or isinstance(source_entry.get("score"), bool)
                    or not 0 <= int(source_entry["score"]) <= 100
                    or not isinstance(source_entry.get("evidence_sha256"), str)
                    or not SAFE_SHA256.fullmatch(
                        str(source_entry["evidence_sha256"])
                    )
                    or not isinstance(historical_hash, str)
                    or historical_hash != expected_historical_hash
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID"
                    )
                accepted_historical.append(historical_row)
            historical_entries = tuple(accepted_historical)
        entries = value.get("entries")
        if not isinstance(entries, list) or len(entries) > 64:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRIES_INVALID")
        accepted: list[dict[str, Any]] = []
        identities: set[tuple[Any, ...]] = set()
        api_required = {
            "node_id",
            "profile_kind",
            "provider_family",
            "model_name",
            "thinking",
            "tier",
            "verdict",
            "score",
            "evidence_sha256",
        }
        cli_required = {
            "node_id",
            "profile_kind",
            "adapter_id",
            "model_name",
            "thinking_mode",
            "verdict",
            "score",
            "evidence_sha256",
        }
        local_required = {
            "node_id",
            "profile_kind",
            "endpoint_kind",
            "model_name",
            "model_digest",
            "verdict",
            "score",
            "evidence_sha256",
        }
        if schema_version in {
            "WorkflowModelExamScoreCatalog-v4",
            "WorkflowModelExamScoreCatalog-v5",
            "WorkflowModelExamScoreCatalog-v6",
            "WorkflowModelExamScoreCatalog-v7",
        }:
            metadata_required = set(WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4)
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
                metadata_required |= set(
                    WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5
                )
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
                metadata_required |= set(
                    WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
                )
            if schema_version == "WorkflowModelExamScoreCatalog-v7":
                metadata_required |= set(
                    WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7
                )
            api_required |= metadata_required
            cli_required |= metadata_required
            local_required |= metadata_required
        for raw in entries:
            if not isinstance(raw, Mapping):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
            row = dict(raw)
            if not {
                "node_id",
                "profile_kind",
                "model_name",
                "verdict",
                "score",
                "evidence_sha256",
            }.issubset(row):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
            common_invalid = (
                not isinstance(row["node_id"], str)
                or not row["node_id"]
                or not isinstance(row["model_name"], str)
                or not SAFE_MODEL.fullmatch(row["model_name"])
                or row["verdict"] not in {"PASS", "FAIL"}
                or isinstance(row["score"], bool)
                or not isinstance(row["score"], int)
                or not 0 <= row["score"] <= 100
                or not isinstance(row["evidence_sha256"], str)
                or not SAFE_SHA256.fullmatch(row["evidence_sha256"])
            )
            if common_invalid:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v4",
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
                score_exact = row.get("score_exact")
                string_fields = (
                    "score_semantics",
                    "exam_category_id",
                    "role_id",
                    "scoring_projection_revision",
                    "comparison_cohort_id",
                )
                if (
                    isinstance(score_exact, bool)
                    or not isinstance(score_exact, (int, float))
                    or not 0.0 <= float(score_exact) <= 100.0
                    or row["score"] != int(round(float(score_exact)))
                    or row.get("qualification_verdict")
                    not in {"PASS", "FAIL", "DISQUALIFIED"}
                    or any(
                        not isinstance(row.get(field), str)
                        or not SAFE_MODEL.fullmatch(str(row[field]))
                        for field in string_fields
                    )
                    or not isinstance(
                        row.get("horizontal_comparison_eligible"), bool
                    )
                    or row.get(
                        "horizontal_comparison_allowed_only_with_same_cohort"
                    )
                    is not True
                    or row.get("cross_category_comparison_forbidden") is not True
                    or row.get("global_ranking_forbidden") is not True
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_SCORE_METADATA_INVALID"
                    )
                allowed_nodes = WORKFLOW_EXAM_ROLE_NODE_BINDINGS.get(
                    str(row["role_id"])
                )
                if (
                    allowed_nodes is None
                    or row["node_id"] not in allowed_nodes
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_ROLE_NODE_MISMATCH"
                    )
                if schema_version in {
                    "WorkflowModelExamScoreCatalog-v5",
                    "WorkflowModelExamScoreCatalog-v6",
                    "WorkflowModelExamScoreCatalog-v7",
                }:
                    operational_verdict = row.get(
                        "operational_eligibility_verdict"
                    )
                    operational_policy = row.get(
                        "operational_policy_revision"
                    )
                    operational_gate = row.get("operational_safety_gate")
                    safety_count = row.get("safety_blocking_residual_count")
                    calibration_count = row.get("calibration_residual_count")
                    count_values_valid = all(
                        value is None
                        or (
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                        )
                        for value in (safety_count, calibration_count)
                    )
                    if (
                        operational_verdict
                        not in {
                            "SUITABLE",
                            "SUITABLE_WITH_CALIBRATION",
                            "SUITABLE_WITH_GOVERNED_DISPOSITION",
                            "NOT_RECOMMENDED",
                            "REJECT",
                            "NOT_ASSESSED",
                        }
                        or not isinstance(operational_policy, str)
                        or not SAFE_MODEL.fullmatch(operational_policy)
                        or operational_gate not in {"PASS", "FAIL", "NOT_ASSESSED"}
                        or not count_values_valid
                        or (
                            operational_verdict == "NOT_ASSESSED"
                            and (
                                operational_gate != "NOT_ASSESSED"
                                or safety_count is not None
                                or calibration_count is not None
                            )
                        )
                        or (
                            operational_verdict
                            in {
                                "SUITABLE",
                                "SUITABLE_WITH_CALIBRATION",
                                "SUITABLE_WITH_GOVERNED_DISPOSITION",
                            }
                            and (
                                row["verdict"] != "PASS"
                                or operational_gate != "PASS"
                                or safety_count != 0
                                or not isinstance(calibration_count, int)
                                or row["role_id"] != "CARD_REVIEWER"
                            )
                        )
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_OPERATIONAL_METADATA_INVALID"
                        )
                if schema_version in {
                    "WorkflowModelExamScoreCatalog-v6",
                    "WorkflowModelExamScoreCatalog-v7",
                }:
                    exact_rate = row.get("exact_case_repair_rate")
                    deficit_rate = row.get(
                        "case_score_deficit_recovery_rate"
                    )
                    collision_count = row.get(
                        "repair_metadata_collision_suspect_count"
                    )
                    adjusted_rate = row.get(
                        "collision_adjusted_repair_diagnostic_rate"
                    )
                    rates = (exact_rate, deficit_rate, adjusted_rate)
                    all_rates_none = all(value is None for value in rates)
                    all_rates_valid = all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and 0.0 <= float(value) <= 1.0
                        for value in rates
                    )
                    reviewer_repair_assessed = (
                        row["role_id"] == "CARD_REVIEWER"
                        and operational_verdict != "NOT_ASSESSED"
                    )
                    if (
                        (
                            reviewer_repair_assessed
                            and (
                                not all_rates_valid
                                or not isinstance(collision_count, int)
                                or isinstance(collision_count, bool)
                                or collision_count < 0
                                or float(adjusted_rate)
                                < float(exact_rate)
                            )
                        )
                        or (
                            not reviewer_repair_assessed
                            and (
                                not all_rates_none
                                or collision_count is not None
                            )
                        )
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_REPAIR_METADATA_INVALID"
                        )
                successor_series = False
                if schema_version == "WorkflowModelExamScoreCatalog-v7":
                    cache_strings = (
                        "executor_ref",
                        "sample_revision",
                        "repair_prompt_projection_revision",
                        "pointer_normalization_revision",
                        "subject_transport_projection_revision",
                        "series_aggregate_method",
                    )
                    series_slots = row.get("series_slots")
                    provenance = row.get("cache_provenance_kind")
                    core_closure_score = row.get("core_closure_score")
                    semantic_repair_rounds = row.get(
                        "semantic_repair_rounds"
                    )
                    failed_exam_item_count = row.get(
                        "failed_exam_item_count"
                    )
                    if (
                        row.get("cache_reuse_status") != "STABLE_REUSE"
                        or provenance
                        not in {
                            "SINGLE_FROZEN_EVIDENCE",
                            "THREE_SLOT_SUCCESSOR_SERIES",
                        }
                        or any(
                            not isinstance(row.get(field), str)
                            or not SAFE_MODEL.fullmatch(str(row[field]))
                            for field in cache_strings
                        )
                        or row.get("core_closure_verdict")
                        not in {
                            "DELTA_PASS",
                            "PASS_WITH_CALIBRATION",
                            "PASS_WITH_GOVERNED_DISPOSITION",
                            "HUMAN_REQUIRED",
                            "NOT_ASSESSED",
                        }
                        or (
                            core_closure_score is not None
                            and (
                                isinstance(core_closure_score, bool)
                                or not isinstance(
                                    core_closure_score, (int, float)
                                )
                                or not 0.0
                                <= float(core_closure_score)
                                <= 100.0
                            )
                        )
                        or (
                            semantic_repair_rounds is not None
                            and (
                                isinstance(semantic_repair_rounds, bool)
                                or not isinstance(
                                    semantic_repair_rounds, int
                                )
                                or semantic_repair_rounds < 0
                            )
                        )
                        or (
                            failed_exam_item_count is not None
                            and (
                                isinstance(failed_exam_item_count, bool)
                                or not isinstance(failed_exam_item_count, int)
                                or failed_exam_item_count < 0
                            )
                        )
                        or not isinstance(series_slots, list)
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_CACHE_METADATA_INVALID"
                        )
                    if provenance == "SINGLE_FROZEN_EVIDENCE":
                        if (
                            series_slots
                            or row["series_aggregate_method"]
                            != "SINGLE_FROZEN_EXAM"
                            or row["sample_revision"] != "NOT_APPLICABLE"
                            or row["repair_prompt_projection_revision"]
                            != "NOT_APPLICABLE"
                            or row["pointer_normalization_revision"]
                            != "NOT_APPLICABLE"
                            or row["subject_transport_projection_revision"]
                            != "NOT_APPLICABLE"
                            or row["core_closure_verdict"]
                            != "NOT_ASSESSED"
                            or core_closure_score is not None
                            or semantic_repair_rounds is not None
                            or failed_exam_item_count is not None
                        ):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_CATALOG_SINGLE_CACHE_INVALID"
                            )
                    else:
                        successor_series = True
                        slot_fields = {
                            "sample_slot",
                            "sample_manifest_sha256",
                            "score_exact",
                            "verdict",
                            "qualification_verdict",
                            "operational_eligibility_verdict",
                            "operational_safety_gate",
                            "safety_blocking_residual_count",
                            "calibration_residual_count",
                            "core_closure_verdict",
                            "core_closure_score",
                            "failed_exam_item_count",
                            "semantic_repair_rounds",
                            "exact_case_repair_rate",
                            "case_score_deficit_recovery_rate",
                            "repair_metadata_collision_suspect_count",
                            "collision_adjusted_repair_diagnostic_rate",
                            "evidence_sha256",
                            "comparison_binding_sha256",
                        }
                        if (
                            row["role_id"] != "CARD_REVIEWER"
                            or row["series_aggregate_method"]
                            != "MEAN_FIRST_PASS_NONLINEAR_ROLE_ABILITY_ACROSS_THREE_DISJOINT_SLOTS"
                            or len(series_slots) != 3
                            or core_closure_score is None
                            or semantic_repair_rounds is None
                            or failed_exam_item_count is None
                        ):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_INCOMPLETE"
                            )
                        slot_numbers: list[int] = []
                        sample_hashes: list[str] = []
                        slot_scores: list[float] = []
                        slot_safety = 0
                        slot_calibration = 0
                        slot_failed = 0
                        slot_semantic_rounds = 0
                        slot_core_scores: list[float] = []
                        slot_exact_rates: list[float] = []
                        slot_deficit_rates: list[float] = []
                        slot_collision_counts = 0
                        slot_adjusted_rates: list[float] = []
                        all_slot_pass = True
                        all_slot_qualification_pass = True
                        expected_operational = "SUITABLE"
                        expected_closure = "DELTA_PASS"
                        for slot in series_slots:
                            if not isinstance(slot, Mapping) or set(slot) != slot_fields:
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_SLOT_INVALID"
                                )
                            slot_number = slot.get("sample_slot")
                            slot_score = slot.get("score_exact")
                            slot_core_score = slot.get(
                                "core_closure_score"
                            )
                            integer_fields = (
                                "safety_blocking_residual_count",
                                "calibration_residual_count",
                                "failed_exam_item_count",
                                "semantic_repair_rounds",
                                "repair_metadata_collision_suspect_count",
                            )
                            rate_fields = (
                                "exact_case_repair_rate",
                                "case_score_deficit_recovery_rate",
                                "collision_adjusted_repair_diagnostic_rate",
                            )
                            if (
                                isinstance(slot_number, bool)
                                or not isinstance(slot_number, int)
                                or slot_number not in {1, 2, 3}
                                or isinstance(slot_score, bool)
                                or not isinstance(slot_score, (int, float))
                                or not 0.0 <= float(slot_score) <= 100.0
                                or isinstance(slot_core_score, bool)
                                or not isinstance(
                                    slot_core_score, (int, float)
                                )
                                or not 0.0
                                <= float(slot_core_score)
                                <= 100.0
                                or slot.get("verdict") not in {"PASS", "FAIL"}
                                or slot.get("qualification_verdict")
                                not in {"PASS", "DISQUALIFIED"}
                                or slot.get("operational_eligibility_verdict")
                                not in {
                                    "SUITABLE",
                                    "SUITABLE_WITH_CALIBRATION",
                                    "SUITABLE_WITH_GOVERNED_DISPOSITION",
                                    "NOT_RECOMMENDED",
                                    "REJECT",
                                }
                                or slot.get("operational_safety_gate")
                                not in {"PASS", "FAIL"}
                                or slot.get("core_closure_verdict")
                                not in {
                                    "DELTA_PASS",
                                    "PASS_WITH_CALIBRATION",
                                    "PASS_WITH_GOVERNED_DISPOSITION",
                                    "HUMAN_REQUIRED",
                                }
                                or any(
                                    isinstance(slot.get(field), bool)
                                    or not isinstance(slot.get(field), int)
                                    or int(slot[field]) < 0
                                    for field in integer_fields
                                )
                                or any(
                                    isinstance(slot.get(field), bool)
                                    or not isinstance(
                                        slot.get(field), (int, float)
                                    )
                                    or not 0.0 <= float(slot[field]) <= 1.0
                                    for field in rate_fields
                                )
                                or float(
                                    slot[
                                        "collision_adjusted_repair_diagnostic_rate"
                                    ]
                                )
                                < float(slot["exact_case_repair_rate"])
                                or any(
                                    not isinstance(slot.get(field), str)
                                    or not SAFE_SHA256.fullmatch(
                                        str(slot[field])
                                    )
                                    for field in (
                                        "sample_manifest_sha256",
                                        "evidence_sha256",
                                        "comparison_binding_sha256",
                                    )
                                )
                            ):
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_SLOT_INVALID"
                                )
                            slot_numbers.append(slot_number)
                            sample_hashes.append(
                                str(slot["sample_manifest_sha256"])
                            )
                            slot_scores.append(float(slot_score))
                            slot_safety += int(
                                slot["safety_blocking_residual_count"]
                            )
                            slot_calibration += int(
                                slot["calibration_residual_count"]
                            )
                            slot_failed += int(slot["failed_exam_item_count"])
                            slot_semantic_rounds += int(
                                slot["semantic_repair_rounds"]
                            )
                            slot_core_scores.append(
                                float(slot_core_score)
                            )
                            slot_exact_rates.append(
                                float(slot["exact_case_repair_rate"])
                            )
                            slot_deficit_rates.append(
                                float(
                                    slot[
                                        "case_score_deficit_recovery_rate"
                                    ]
                                )
                            )
                            slot_collision_counts += int(
                                slot[
                                    "repair_metadata_collision_suspect_count"
                                ]
                            )
                            slot_adjusted_rates.append(
                                float(
                                    slot[
                                        "collision_adjusted_repair_diagnostic_rate"
                                    ]
                                )
                            )
                            all_slot_pass &= slot["verdict"] == "PASS"
                            all_slot_qualification_pass &= (
                                slot["qualification_verdict"] == "PASS"
                            )
                            if slot["operational_eligibility_verdict"] in {
                                "NOT_RECOMMENDED",
                                "REJECT",
                            }:
                                expected_operational = "NOT_RECOMMENDED"
                            elif (
                                expected_operational != "NOT_RECOMMENDED"
                                and slot[
                                    "operational_eligibility_verdict"
                                ] == "SUITABLE_WITH_GOVERNED_DISPOSITION"
                            ):
                                expected_operational = (
                                    "SUITABLE_WITH_GOVERNED_DISPOSITION"
                                )
                            elif (
                                expected_operational == "SUITABLE"
                                and slot["operational_eligibility_verdict"]
                                == "SUITABLE_WITH_CALIBRATION"
                            ):
                                expected_operational = "SUITABLE_WITH_CALIBRATION"
                            if slot["core_closure_verdict"] == "HUMAN_REQUIRED":
                                expected_closure = "HUMAN_REQUIRED"
                            elif (
                                expected_closure != "HUMAN_REQUIRED"
                                and slot["core_closure_verdict"]
                                == "PASS_WITH_GOVERNED_DISPOSITION"
                            ):
                                expected_closure = (
                                    "PASS_WITH_GOVERNED_DISPOSITION"
                                )
                            elif (
                                expected_closure == "DELTA_PASS"
                                and slot["core_closure_verdict"]
                                == "PASS_WITH_CALIBRATION"
                            ):
                                expected_closure = "PASS_WITH_CALIBRATION"
                        mean = lambda values: round(sum(values) / len(values), 9)
                        if (
                            sorted(slot_numbers) != [1, 2, 3]
                            or len(set(sample_hashes)) != 3
                            or abs(float(row["score_exact"]) - mean(slot_scores))
                            > 0.000001
                            or row["score"]
                            != int(round(float(row["score_exact"])))
                            or row["verdict"]
                            != ("PASS" if all_slot_pass else "FAIL")
                            or row["qualification_verdict"]
                            != (
                                "PASS"
                                if all_slot_qualification_pass
                                else "DISQUALIFIED"
                            )
                            or row["operational_eligibility_verdict"]
                            != expected_operational
                            or row["operational_safety_gate"]
                            != ("PASS" if slot_safety == 0 else "FAIL")
                            or row["safety_blocking_residual_count"]
                            != slot_safety
                            or row["calibration_residual_count"]
                            != slot_calibration
                            or row["failed_exam_item_count"] != slot_failed
                            or row["semantic_repair_rounds"]
                            != slot_semantic_rounds
                            or row["core_closure_verdict"]
                            != expected_closure
                            or abs(
                                float(row["core_closure_score"])
                                - mean(slot_core_scores)
                            )
                            > 0.000001
                            or abs(
                                float(row["exact_case_repair_rate"])
                                - mean(slot_exact_rates)
                            )
                            > 0.000001
                            or abs(
                                float(
                                    row[
                                        "case_score_deficit_recovery_rate"
                                    ]
                                )
                                - mean(slot_deficit_rates)
                            )
                            > 0.000001
                            or row[
                                "repair_metadata_collision_suspect_count"
                            ]
                            != slot_collision_counts
                            or abs(
                                float(
                                    row[
                                        "collision_adjusted_repair_diagnostic_rate"
                                    ]
                                )
                                - mean(slot_adjusted_rates)
                            )
                            > 0.000001
                        ):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_AGGREGATE_INVALID"
                            )
                    cache_binding = row.get("cache_binding_sha256")
                    expected_cache_binding = hashlib.sha256(
                        json.dumps(
                            {
                                key: item
                                for key, item in row.items()
                                if key != "cache_binding_sha256"
                            },
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest().upper()
                    if (
                        not isinstance(cache_binding, str)
                        or cache_binding != expected_cache_binding
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_CACHE_BINDING_INVALID"
                        )
                comparable_fields = (
                    "reference_pack_id",
                    "reference_pack_revision",
                    "reference_pack_sha256",
                    "scoring_protocol_revision",
                    "scoring_protocol_sha256",
                    "comparison_binding_sha256",
                )
                if row["horizontal_comparison_eligible"]:
                    if (
                        any(
                            not isinstance(row.get(field), str)
                            or not row[field]
                            for field in comparable_fields
                        )
                        or not SAFE_SHA256.fullmatch(
                            str(row["reference_pack_sha256"])
                        )
                        or not SAFE_SHA256.fullmatch(
                            str(row["scoring_protocol_sha256"])
                        )
                        or not SAFE_SHA256.fullmatch(
                            str(row["comparison_binding_sha256"])
                        )
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_COMPARISON_BINDING_INVALID"
                        )
                    binding = {
                        "exam_category_id": row["exam_category_id"],
                        "reference_pack_id": row["reference_pack_id"],
                        "reference_pack_revision": row[
                            "reference_pack_revision"
                        ],
                        "reference_pack_sha256": row[
                            "reference_pack_sha256"
                        ],
                        "role_id": row["role_id"],
                        "score_semantics": row["score_semantics"],
                        "scoring_projection_revision": row[
                            "scoring_projection_revision"
                        ],
                        "scoring_protocol_revision": row[
                            "scoring_protocol_revision"
                        ],
                        "scoring_protocol_sha256": row[
                            "scoring_protocol_sha256"
                        ],
                    }
                    if successor_series:
                        binding.update(
                            {
                                "executor_ref": row["executor_ref"],
                                "sample_revision": row["sample_revision"],
                                "sample_manifest_sha256s": [
                                    slot["sample_manifest_sha256"]
                                    for slot in sorted(
                                        row["series_slots"],
                                        key=lambda item: item["sample_slot"],
                                    )
                                ],
                                "repair_prompt_projection_revision": row[
                                    "repair_prompt_projection_revision"
                                ],
                                "pointer_normalization_revision": row[
                                    "pointer_normalization_revision"
                                ],
                                "subject_transport_projection_revision": row[
                                    "subject_transport_projection_revision"
                                ],
                            }
                        )
                    expected_binding = hashlib.sha256(
                        json.dumps(
                            binding,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest().upper()
                    if (
                        row["comparison_binding_sha256"]
                        != expected_binding
                        or row["comparison_cohort_id"]
                        != (
                            f"{row['role_id']}:SERIES:{expected_binding}"
                            if successor_series
                            else f"{row['role_id']}:{expected_binding}"
                        )
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_COMPARISON_BINDING_INVALID"
                        )
                elif (
                    any(row.get(field) is not None for field in comparable_fields)
                    or row["comparison_cohort_id"]
                    != f"LEGACY_SELF:{row['evidence_sha256']}"
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_LEGACY_SCOPE_INVALID"
                    )
            if row.get("profile_kind") == "API":
                if (
                    set(row) != api_required
                    or row["provider_family"]
                    not in {
                        "OPENAI_COMPATIBLE",
                        "ANTHROPIC",
                        "OPENAI",
                        "GEMINI",
                    }
                    or not isinstance(row["thinking"], bool)
                    or not isinstance(row["tier"], str)
                    or not SAFE_THINKING.fullmatch(row["tier"])
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
                identity = (
                    row["node_id"],
                    "API",
                    row["provider_family"],
                    row["model_name"],
                    row["thinking"],
                    row["tier"],
                )
            elif row.get("profile_kind") == "CLI":
                if (
                    schema_version
                    not in {
                        "WorkflowModelExamScoreCatalog-v2",
                        "WorkflowModelExamScoreCatalog-v3",
                        "WorkflowModelExamScoreCatalog-v4",
                        "WorkflowModelExamScoreCatalog-v5",
                        "WorkflowModelExamScoreCatalog-v6",
                        "WorkflowModelExamScoreCatalog-v7",
                    }
                    or set(row) != cli_required
                    or not isinstance(row["adapter_id"], str)
                    or not SAFE_THINKING.fullmatch(row["adapter_id"])
                    or not isinstance(row["thinking_mode"], str)
                    or not SAFE_THINKING.fullmatch(row["thinking_mode"])
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
                identity = (
                    row["node_id"],
                    "CLI",
                    row["adapter_id"],
                    row["model_name"],
                    row["thinking_mode"],
                )
            elif row.get("profile_kind") == "LOCAL":
                if (
                    schema_version
                    not in {
                        "WorkflowModelExamScoreCatalog-v3",
                        "WorkflowModelExamScoreCatalog-v4",
                        "WorkflowModelExamScoreCatalog-v5",
                        "WorkflowModelExamScoreCatalog-v6",
                        "WorkflowModelExamScoreCatalog-v7",
                    }
                    or set(row) != local_required
                    or row["endpoint_kind"] != "ollama"
                    or not isinstance(row["model_digest"], str)
                    or not SAFE_SHA256.fullmatch(row["model_digest"])
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
                identity = (
                    row["node_id"],
                    "LOCAL",
                    row["endpoint_kind"],
                    row["model_name"],
                    row["model_digest"],
                )
            else:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
            if identity in identities:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE")
            identities.add(identity)
            accepted.append(row)
        return tuple(accepted), historical_entries

    def _frozen_workflow_exam_row(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        active_executor_refs = {
            str(getattr(executor, "EXECUTOR_REF"))
            for executor in self._workflow_exam_executors
            if isinstance(getattr(executor, "EXECUTOR_REF", None), str)
        }
        recorded = self._exam_score_history.lookup(node, target, active_executor_refs)
        if recorded is None:
            recorded = self._exam_score_history.recover_completed_embedding(node, target, active_executor_refs)
        if recorded is not None:
            return recorded
        if target.get("adapter_id")=="command_template":
            return None

        def reusable(candidate: Mapping[str, Any]) -> bool:
            if candidate.get(
                "cache_reuse_status", "STABLE_REUSE"
            ) != "STABLE_REUSE":
                return False
            if candidate.get("cache_reuse_requires_executor_ref_match") is True:
                return candidate.get("executor_ref") in active_executor_refs
            return True

        if target.get("kind") == "LOCAL":
            endpoint_kind = target.get("endpoint_kind")
            model_name = target.get("model_name")
            model_digest = target.get("model_digest")
            if (
                endpoint_kind != "ollama"
                or not isinstance(model_name, str)
                or not isinstance(model_digest, str)
                or not SAFE_SHA256.fullmatch(model_digest.upper())
            ):
                return None
            identity = (
                node.get("node_id"),
                "LOCAL",
                endpoint_kind,
                model_name,
                model_digest.upper(),
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "LOCAL"
                    and (
                        candidate["node_id"],
                        "LOCAL",
                        candidate["endpoint_kind"],
                        candidate["model_name"],
                        candidate["model_digest"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") == "CLI":
            adapter_id = target.get("adapter_id")
            model_name = target.get("model_name")
            thinking_mode = target.get("thinking_mode")
            if not all(
                isinstance(value, str) and bool(value)
                for value in (adapter_id, model_name, thinking_mode)
            ):
                return None
            identity = (
                node.get("node_id"),
                "CLI",
                adapter_id,
                model_name,
                thinking_mode,
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "CLI"
                    and (
                        candidate["node_id"],
                        "CLI",
                        candidate["adapter_id"],
                        candidate["model_name"],
                        candidate["thinking_mode"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") != "API":
            return None
        try:
            api_profile = _api_profile(target)
        except ValueError:
            return None
        if api_profile is None:
            return None
        model_name = target.get("model_name")
        thinking = target.get("thinking")
        tier = target.get("tier")
        if (
            not isinstance(model_name, str)
            or not isinstance(thinking, bool)
            or not isinstance(tier, str)
        ):
            return None
        identity = (
            node.get("node_id"),
            "API",
            api_profile.provider_family,
            model_name,
            thinking,
            tier,
        )
        row = next(
            (
                candidate
                for candidate in self._workflow_exam_entries
                if reusable(candidate)
                and candidate.get("profile_kind") == "API"
                and (
                    candidate["node_id"],
                    candidate["profile_kind"],
                    candidate["provider_family"],
                    candidate["model_name"],
                    candidate["thinking"],
                    candidate["tier"],
                )
                == identity
            ),
            None,
        )
        return row

    def workflow_exam_score_catalog(self) -> Mapping[str, Any]:
        return {
            "schema_version": "WorkflowModelExamScoreCatalogProjection-v2",
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "entries": [dict(row) for row in self._workflow_exam_entries],
            "historical_entries": [
                dict(row) for row in self._workflow_exam_historical_entries
            ],
        }

    def cached_workflow_node_score(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        resolved_target = self._resolve_exam_target(target)
        row = self._frozen_workflow_exam_row(node, resolved_target)
        return dict(row) if row is not None else None

    @staticmethod
    def _plan_hash(plan: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                plan,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is not None:
            plan: dict[str, Any] = {
                "schema_version": "WorkflowModelExamPlan-v2",
                "status": "READY",
                "mode": "FROZEN_MODEL_EXAM_SCORE_REUSE",
                "reason": "FROZEN_MODEL_EXAM_SCORE_AVAILABLE",
                "node_id": node.get("node_id"),
                "model_name": resolved_target.get("model_name"),
                "maximum_model_calls": 0,
                "budget_cap_cny": 0.0,
                "reference_regression_only": True,
                "qualification_eligible": False,
                "exam_evidence_sha256": row["evidence_sha256"],
                "authorization_required": False,
                "external_network_calls": 0,
                "provider_calls": 0,
                "external_model_calls": 0,
            }
            plan.update(
                {
                    field: row[field]
                    for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                    if field in row
                }
            )
            plan["plan_sha256"] = self._plan_hash(plan)
            return plan
        accepted_context = dict(context or {})
        executor_plan_diagnostics: list[dict[str, str]] = []
        from .tiered_exam import tier_executor
        for original_executor in self._workflow_exam_executors:
            executor = tier_executor(original_executor, accepted_context)
            executor_ref = getattr(
                executor, "EXECUTOR_REF", type(executor).__name__
            )
            if not isinstance(executor_ref, str) or not SAFE_MODEL.fullmatch(
                executor_ref
            ):
                executor_ref = type(executor).__name__
            try:
                plan = executor.plan(node, resolved_target, accepted_context)
            except Exception as exc:
                reason = type(exc).__name__
                missing_module = getattr(exc, "name", None)
                if isinstance(missing_module, str) and SAFE_MODEL.fullmatch(
                    missing_module
                ):
                    reason = f"{reason}:{missing_module}"
                elif (
                    exc.args
                    and isinstance(exc.args[0], str)
                    and SAFE_REASON_CODE.fullmatch(exc.args[0])
                ):
                    reason = exc.args[0]
                executor_plan_diagnostics.append(
                    {
                        "executor_ref": executor_ref,
                        "status": "ERROR",
                        "reason": reason,
                    }
                )
                continue
            if isinstance(plan, Mapping) and plan.get("status") == "READY":
                return dict(plan)
            plan_reason = (
                plan.get("reason") if isinstance(plan, Mapping) else None
            )
            executor_plan_diagnostics.append(
                {
                    "executor_ref": executor_ref,
                    "status": "NOT_READY",
                    "reason": (
                        plan_reason
                        if isinstance(plan_reason, str)
                        and SAFE_REASON_CODE.fullmatch(plan_reason)
                        else "WORKFLOW_NODE_EXAM_EXECUTOR_PLAN_NOT_READY"
                    ),
                }
            )
        return {
            "schema_version": "WorkflowModelExamPlan-v2",
            "status": "NOT_AVAILABLE",
            "mode": "NONE",
            "reason": "WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
            "executor_plan_diagnostics": executor_plan_diagnostics,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        result = self._base(
            "WORKFLOW_NODE",
            status="NOT_RUN",
            reason="WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
        )
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is None:
            accepted_context = dict(context or {})
            from .tiered_exam import tier_executor, project_tier_result
            for original_executor in self._workflow_exam_executors:
                executor = tier_executor(original_executor, accepted_context)
                try:
                    plan = executor.plan(node, resolved_target, accepted_context)
                except Exception:
                    continue
                if not isinstance(plan, Mapping) or plan.get("status") != "READY":
                    continue
                try:
                    parameters = signature(executor.execute).parameters
                    from .exam_jobs import observed_call
                    kwargs: dict[str, Any] = {
                        "authorization": authorization,
                        "structured_chat": observed_call(self.execute_structured_chat, "structured_chat"),
                    }
                    if "embedding_call" in parameters:
                        kwargs["embedding_call"] = observed_call(self.execute_embeddings, "embedding")
                    if "ocr_call" in parameters:
                        kwargs["ocr_call"] = observed_call(self.execute_ocr_image, "ocr")
                    if "rerank_call" in parameters:
                        kwargs["rerank_call"] = observed_call(self.execute_rerank, "rerank")
                    dynamic = executor.execute(
                        node,
                        resolved_target,
                        accepted_context,
                        **kwargs,
                    )
                except Exception:
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                if not isinstance(dynamic, Mapping):
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                projected = project_tier_result(dynamic, plan)
                try:
                    self._exam_score_history.record(
                        node, resolved_target, projected,
                        {getattr(e, "EXECUTOR_REF", None) for e in self._workflow_exam_executors},
                    )
                except (OSError, ValueError, TypeError):
                    # The answer and immutable run survive an auxiliary cache failure.
                    projected["score_cache_write_failed"] = True
                return projected
            return result
        model_name = resolved_target.get("model_name")
        result.update(
            status=row["verdict"],
            score=row["score"],
            reason="FROZEN_MODEL_EXAM_SCORE_REUSED",
            requested_model=model_name,
            returned_model=model_name,
            exam_evidence_sha256=row["evidence_sha256"],
            **{
                field: row[field]
                for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                if field in row
            },
        )
        return result
